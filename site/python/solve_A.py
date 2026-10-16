"""G绿色通道序列 + P概率图像序列 -> 逐对A、筛选报告和汇总。

核心保留：固定均匀空间分箱、当前/前1/前2帧历史、SVD伪逆、
目标SVD因子、最小范数基解、折入L的微小非负修正、QR秩检查、A=L@R保存。
没有岭回归、训练、零空间升秩或共享A；新帧的概率性质不作保证。

例：python solve_A.py --lc-file green.npy --target-file probability.npy --output-root results
NPY的G=(T,H,W)/(V,T,H,W)，P=(T,Hp,Wp)。NPZ同时支持像素列表或已分箱缓存。
任意G/P不代表必然有满足条件的A；输入F非满行秩时记录rejected而不修改核心求解。
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")
for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"
import numpy as np
from bin_pixels import (BIN_WIDTH, build_features, finite_array, load_lc, prepare_green_images,
                        prepare_lc, read_array_input, save_prepared, validate_time)


def prepare_target(source, lc_time, lc_time_is_index=False, time_align="auto", normalize=False,
                   kind=None, mass_tolerance=1e-10):
    """将概率图像转为(T,K)；只作输入转换，不改变A的求解公式。"""
    P = finite_array(source["P"], "P")
    if P.ndim not in (2,3) or min(P.shape) < 1:
        raise ValueError("P必须为(T,K)概率矩阵或(T,H,W)概率图像序列")
    if np.any(P < 0):
        raise ValueError("P含负值；概率输入必须非负")
    shape = np.array(P.shape[1:]) if P.ndim == 3 else np.asarray(source.get("image_shape", []), dtype=int)
    if P.ndim == 3:
        yy,xx = np.indices(P.shape[1:])
        xy = np.column_stack([xx.ravel(),yy.ravel()])
    else:
        xy = np.asarray(source["xy"]) if "xy" in source else None
    P = P.reshape(len(P), -1)
    cells = P.shape[1]
    active = np.asarray(source.get("active", np.arange(cells)))
    if active.ndim not in (1,2) or len(active) != cells:
        raise ValueError("active必须有K个网格ID或K行坐标")
    if xy is not None and (xy.shape != (cells,2) or not np.isfinite(xy).all()):
        raise ValueError("目标xy必须为(K,2)有限网格坐标")
    if shape.size and (shape.shape != (2,) or np.any(shape <= 0)):
        raise ValueError("image_shape须为正的[H,W]")
    area = finite_array(source.get("cell_area", 1.), "cell_area")
    if area.ndim > 0 and area.size == cells:
        area = area.reshape(cells)
    if area.shape not in ((),(cells,)) or np.any(area <= 0):
        raise ValueError("cell_area须为正标量或K个正网格面积")
    kind = kind or str(np.asarray(source.get("kind", "mass")).item())
    if kind not in ("mass","density"):
        raise ValueError("kind须为mass或density")
    if kind == "density":
        if "cell_area" not in source:
            raise ValueError("密度输入必须明确提供cell_area；rho*area为网格积分的中点近似")
        P = P * area
    mass_before = P.sum(axis=1)
    if not np.isfinite(mass_before).all() or np.any(mass_before <= 0):
        raise ValueError("P存在零质量帧或质量溢出")
    if normalize:
        P = P / mass_before[:,None]
    elif np.max(np.abs(mass_before-1)) >= mass_tolerance:
        raise ValueError("P每帧概率和不为1；若需要按ROI内总质量归一化，请显式指定--normalize-target")

    frames = len(lc_time)
    # 旧缓存LC_time_s优先用于与液晶物理时间匹配；model_time可以是独立模拟时间。
    time_value = source.get("LC_time_s", source.get("time", source.get("model_time")))
    target_time = None if time_value is None else validate_time(time_value, len(P), "P time")
    mode = time_align
    if mode == "auto":
        if target_time is None and lc_time_is_index:
            mode = "frame"
        elif target_time is not None and not lc_time_is_index:
            mode = "exact"
        else:
            raise ValueError("G/P只有一侧提供时间；请补齐时间或显式--time-align frame")
    interpolated = False
    if mode == "frame":
        if len(P) != frames:
            raise ValueError(f"逐帧匹配要求G/P帧数相同，当前{frames}/{len(P)}")
        model_time = np.asarray(source.get("model_time", target_time if target_time is not None else lc_time))
        validate_time(model_time, frames, "model_time")
    elif mode == "exact":
        if target_time is None or lc_time_is_index:
            raise ValueError("exact时间匹配要求G/P均提供物理时间数组")
        if target_time.shape != np.asarray(lc_time).shape or not np.allclose(target_time,lc_time,rtol=0,atol=1e-12):
            raise ValueError("G/P时间点不一致；请对齐后输入，或显式--time-align linear")
        model_time = np.asarray(source.get("model_time", target_time))
        validate_time(model_time, frames, "model_time")
    elif mode == "linear":
        if target_time is None or lc_time_is_index:
            raise ValueError("linear时间插值要求G/P均提供物理时间数组")
        if lc_time[0] < target_time[0] or lc_time[-1] > target_time[-1]:
            raise ValueError("P时间范围不能覆盖G；不允许外推")
        if len(target_time) == 1:
            if frames != 1 or lc_time[0] != target_time[0]:
                raise ValueError("单帧目标无法进行时间插值")
        else:
            right = np.searchsorted(target_time,lc_time,side="right").clip(1,len(target_time)-1)
            left = right-1
            weight = (lc_time-target_time[left])/(target_time[right]-target_time[left])
            P = P[left]*(1-weight[:,None])+P[right]*weight[:,None]
        original_model_time = source.get("model_time", target_time)
        original_model_time = validate_time(original_model_time,len(target_time),"model_time")
        model_time = np.interp(lc_time,target_time,original_model_time)
        interpolated = True
    else:
        raise ValueError("time_align须为auto/frame/exact/linear")
    # 与原项目活动网格思想一致：仅剔除所有帧中质量都严格为0的列。
    keep = np.any(P != 0,axis=0)
    P = P[:,keep]
    active = active[keep]
    if xy is not None:
        xy = xy[keep]
    if area.ndim:
        area = area[keep]
    cached = all(key in source for key in ("target_U","target_L0"))
    if cached and not normalize and kind == "mass" and not interpolated and keep.all():
        U = finite_array(source["target_U"], "target_U")
        L0 = finite_array(source["target_L0"], "target_L0")
        if U.shape != (frames,frames) or L0.shape != (P.shape[1],frames):
            raise ValueError("目标缓存SVD因子尺寸错误")
        if not np.allclose(U.T@U,np.eye(frames),rtol=0,atol=1e-10) or np.max(np.abs(U@L0.T-P)) >= 1e-10:
            raise ValueError("目标缓存SVD因子与P不一致")
        s = np.linalg.svd(P,compute_uv=False)
    else:
        # 保留T维完整时间基底，即使K<T或P低秩也不额外截断奇异值。
        U,s,Vh = np.linalg.svd(P,full_matrices=False)
        if U.shape[1] < frames:
            # 避免full_matrices=True在K很大时生成K×K的Vh。
            U = np.linalg.qr(U,mode="complete")[0]
            L0 = P.T @ U
        else:
            L0 = Vh.T * s
    target_rank = int(np.count_nonzero(s>max(P.shape)*np.finfo(float).eps*s[0]))
    result = dict(P=P,target_U=U,target_L0=L0,rank=np.array(target_rank),active=active,
                  cell_area=area,model_time=model_time,LC_time_s=np.asarray(lc_time),image_shape=shape,
                  time_alignment=np.array(mode),LC_time_is_frame_index=np.array(lc_time_is_index),
                  target_normalized=np.array(normalize),source_kind=np.array(kind),
                  original_mass_min=np.array(mass_before.min()),original_mass_max=np.array(mass_before.max()),
                  source_cells=np.array(cells))
    if xy is not None:
        result["target_xy"] = xy
    return result


def input_pseudoinverse(F):
    U,singular_values,Vh = np.linalg.svd(F,full_matrices=False)
    tolerance = max(F.shape)*np.finfo(float).eps*singular_values[0]
    rank = int(np.count_nonzero(singular_values>tolerance))
    if rank != F.shape[0]:
        raise ValueError(f"F秩={rank}，帧数={F.shape[0]}；原满行秩插值架构不适用，不能保证任意P可解")
    return (Vh.T/singular_values)@U.T,singular_values


def factor_singular_values(L,R):
    _,left_core = np.linalg.qr(L,mode="reduced")
    _,right_core = np.linalg.qr(R.T,mode="reduced")
    return np.linalg.svd(left_core@right_core.T,compute_uv=False)


def solve_mapping(F,target,F_plus,input_singular_values):
    """原核心公式保持不变；把写死的50替换为实际帧数，记录实际目标/A秩。"""
    P,Ut,L0 = target["P"],target["target_U"],target["target_L0"]
    frames = F.shape[0]
    R = Ut.T @ F_plus.T
    Z = F @ R.T
    raw_probability = Z @ L0.T
    a = Ut.T @ np.ones(frames)
    margin = 8*np.finfo(float).eps*max(1,float(np.abs(raw_probability).max()))
    offset = np.maximum(-raw_probability.min(axis=0),0)+margin
    scale = 1+float(offset.sum())
    L = (L0+offset[:,None]*a[None,:])/scale
    probability = Z @ L.T
    singular_values = factor_singular_values(L,R)
    tolerance = max(len(L),R.shape[1])*np.finfo(float).eps*singular_values[0]
    rank = int(np.count_nonzero(singular_values>tolerance))
    positive = singular_values[singular_values>tolerance]
    mass_error = float(np.max(np.abs(probability.sum(axis=1)-1)))
    difference = probability-P
    l1_percent = 100*np.abs(difference).sum(axis=1)
    l2_percent = 100*np.linalg.norm(difference,axis=1)/np.linalg.norm(P,axis=1)
    norm_squared = np.sum((L.T@L)*(R@R.T))
    arrays = dict(L=L,R=R,shape=np.array([len(L),F.shape[1]]),rank=np.array(rank),
                  A_singular_values=singular_values,A_rank_tolerance=np.array(tolerance),
                  active_cells=target["active"],numerical_offset=offset,mass_coeff_a=a,
                  correction_scale=np.array(scale),input_rank=np.array(frames),
                  input_singular_values=input_singular_values,cell_area=target["cell_area"],
                  storage_kind=np.array("A=L@R; float64; no extra rank truncation or quantization"),
                  uses_bias=np.array(False),requires_output_projection=np.array(False))
    metrics = dict(A_rows=len(L),A_columns=F.shape[1],A_rank=rank,target_rank=int(target["rank"]),input_rank=frames,
                   input_condition=float(input_singular_values[0]/input_singular_values[-1]),
                   A_Frobenius_norm=float(np.sqrt(max(norm_squared,0))),
                   A_condition=float(positive[0]/positive[-1]) if positive.size else None,
                   maximum_seed_fit_abs=float(np.max(np.abs(raw_probability-P))),
                   maximum_fit_abs=float(np.max(np.abs(difference))),numerical_offset_total=float(offset.sum()),
                   relative_L1_percent_mean=float(l1_percent.mean()),relative_L1_percent_max_frame=float(l1_percent.max()),
                   TV_percent_mean=float(l1_percent.mean()/2),relative_L2_percent_mean=float(l2_percent.mean()),
                   mass_RMSE=float(np.sqrt(np.mean(difference**2))),minimum_probability=float(probability.min()),
                   maximum_probability_sum_error=mass_error)
    return arrays,probability,metrics


def check_requirements(metrics,required_rank=None,max_fit_abs=1e-10,max_mass_error=1e-10,max_offset=1e-10):
    reasons = []
    if not all(np.isfinite(value) for value in metrics.values() if value is not None):
        reasons.append("结果含非有限数值")
    if metrics["minimum_probability"] < 0:
        reasons.append("训练帧存在负概率")
    if metrics["maximum_probability_sum_error"] >= max_mass_error:
        reasons.append("训练帧概率和误差超限")
    if max(metrics["maximum_fit_abs"],metrics["maximum_seed_fit_abs"]) >= max_fit_abs:
        reasons.append("拟合误差超限")
    if metrics["numerical_offset_total"] >= max_offset:
        reasons.append("非负修正过大，不再是微小浮点修正")
    if required_rank is not None and metrics["A_rank"] != required_rank:
        reasons.append(f"A秩{metrics['A_rank']}不满足要求{required_rank}")
    return reasons


def solve_from_images(G,P,*,mask=None,lc_time=None,target_time=None,bin_width=BIN_WIDTH,
                      normalize_target=False,time_align="auto",required_rank=None):
    """内存入口，单组图像；返回arrays、prediction、metrics、target、LC预处理信息。"""
    lc = prepare_green_images(G,mask,lc_time,bin_width)
    if len(lc["F"]) != 1:
        raise ValueError("此内存接口每次处理一组G；批量数据请循环调用或使用CLI")
    source = {"P":P}
    if target_time is not None:
        source["time"] = target_time
    target = prepare_target(source,lc["time"],lc["time_is_frame_index"],time_align,normalize_target)
    arrays,prediction,metrics = solve_mapping(lc["F"][0],target,*input_pseudoinverse(lc["F"][0]))
    reasons = check_requirements(metrics,required_rank)
    if reasons:
        raise ArithmeticError("；".join(reasons))
    return arrays,prediction,metrics,target,lc


def safe_name(value):
    name = str(value)
    if not name or name in (".","..") or any(c in name for c in '/\\:*?"<>|'):
        raise ValueError(f"输入标识不能用作输出文件名：{name!r}")
    return name


def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lc-file","--lc-files",dest="lc_files",nargs="+",type=Path)
    parser.add_argument("--target-file","--target-files",dest="target_files",nargs="+",type=Path)
    parser.add_argument("--lc-mask",type=Path,help="与G同尺寸的固定0/1 NPY掩膜")
    parser.add_argument("--bin-width",type=float,default=BIN_WIDTH)
    parser.add_argument("--time-align",choices=("auto","frame","exact","linear"),default="auto")
    parser.add_argument("--normalize-target",action="store_true",help="显式对每帧P归一化；零质量帧仍拒绝")
    parser.add_argument("--target-kind",choices=("mass","density"),default=None)
    parser.add_argument("--required-rank",type=int,help="例如50；未指定时仅记录A实际秩")
    parser.add_argument("--max-fit-abs",type=float,default=1e-10)
    parser.add_argument("--max-mass-error",type=float,default=1e-10)
    parser.add_argument("--max-offset",type=float,default=1e-10)
    parser.add_argument("--data-root",type=Path,help="兼容旧项目：自动读取原分箱缓存和data/*/target.npz")
    parser.add_argument("--binning-root",type=Path,help="兼容旧分箱目录")
    parser.add_argument("--models",nargs="+",help="旧目录模式的任意模型名")
    parser.add_argument("--videos",nargs="+",type=int,help="各G文件内从1开始的视频序号")
    parser.add_argument("--output-root",type=Path,required=True)
    parser.add_argument("--overwrite",action="store_true")
    args = parser.parse_args()
    if args.required_rank is not None and args.required_rank < 1:
        parser.error("--required-rank必须为正整数")
    if any(not np.isfinite(value) or value <= 0 for value in (args.max_fit_abs,args.max_mass_error,args.max_offset)):
        parser.error("验收阈值必须为正的有限数值")
    if not np.isfinite(args.bin_width) or args.bin_width <= 0:
        parser.error("--bin-width必须为正数")
    if args.data_root:
        if args.lc_files or args.target_files:
            parser.error("--data-root旧模式不能与--lc-file/--target-file混用")
        binning = (args.binning_root or args.data_root)/"binning"
        candidates = [binning/name for name in ("binned_green_all100.npz","binned_green.npz","binned_green_selected.npz")]
        args.lc_files = [next((path for path in candidates if path.is_file()),candidates[0])]
        try:
            names = [safe_name(name) for name in args.models] if args.models else sorted(
                path.parent.name for path in (args.data_root/"data").glob("*/target.npz"))
        except ValueError as error:
            parser.error(str(error))
        args.target_files = [args.data_root/"data"/name/"target.npz" for name in dict.fromkeys(names)]
        if args.time_align == "auto":
            args.time_align = "frame"  # 原工程默认依靠帧序映射，与原行为一致。
    elif args.binning_root or args.models:
        parser.error("--binning-root/--models只用于--data-root旧模式")
    if not args.lc_files or not args.target_files:
        parser.error("须同时提供--lc-file和--target-file，或提供--data-root")
    output = args.output_root.resolve()
    if args.data_root and (output == args.data_root.resolve() or output.is_relative_to(args.data_root.resolve())):
        parser.error("输出目录须位于原项目之外")
    for path in args.lc_files+args.target_files:
        resolved = path.resolve()
        if resolved == output or (resolved.is_dir() and output.is_relative_to(resolved)):
            parser.error("输出目录不能位于输入图像目录中")
    if output.exists() and any(output.iterdir()) and not args.overwrite:
        parser.error("输出目录非空；请选择新目录或显式--overwrite")
    try:
        mask = np.load(args.lc_mask,allow_pickle=False) if args.lc_mask else None
        lcs = [(f"g{i+1:03d}_{safe_name(path.stem)}",load_lc(path,args.bin_width,mask))
               for i,path in enumerate(args.lc_files)]
        sources = [(f"p{i+1:03d}_{safe_name(path.parent.name if path.name == 'target.npz' else path.stem)}",
                    read_array_input(path,"P")) for i,path in enumerate(args.target_files)]
        for _,lc in lcs:
            videos = list(dict.fromkeys(args.videos if args.videos is not None else range(1,len(lc["F"])+1)))
            if any(video<1 or video>len(lc["F"]) for video in videos):
                raise ValueError(f"视频序号超出当前G文件范围1..{len(lc['F'])}")
            for video in lc["video_ids"]:
                safe_name(video)
    except (ValueError,OSError,KeyError) as error:
        parser.error(str(error))
    output.mkdir(parents=True,exist_ok=True)
    records = []
    target_cache = {}
    for lc_id,lc in lcs:
        save_prepared(output/"inputs"/lc_id/"binning",lc)
        videos = list(dict.fromkeys(args.videos if args.videos is not None else range(1,len(lc["F"])+1)))
        # 目标SVD和输入伪逆各只计算一次；随后复用进行笛卡尔积配对。
        prepared_targets = {}
        for target_id,source in sources:
            try:
                cache_key = (target_id,lc["time"].tobytes(),lc["time_is_frame_index"])
                if cache_key not in target_cache:
                    target_cache[cache_key] = prepare_target(source,lc["time"],lc["time_is_frame_index"],args.time_align,
                                                           args.normalize_target,args.target_kind)
                target = target_cache[cache_key]
                prepared_targets[target_id] = target
                folder = output/"inputs"/lc_id/target_id
                folder.mkdir(parents=True,exist_ok=True)
                np.savez_compressed(folder/"target.npz",**target)
            except (ValueError,KeyError,np.linalg.LinAlgError) as error:
                prepared_targets[target_id] = str(error)
        for video in videos:
            video_id = safe_name(lc["video_ids"][video-1])
            F = lc["F"][video-1]
            solver = None
            solver_error = ""
            try:
                solver = input_pseudoinverse(F)
            except (ValueError,np.linalg.LinAlgError) as error:
                solver_error = str(error)
            for target_id,target in prepared_targets.items():
                record = dict(lc_id=lc_id,model_id=target_id,video_id=video_id,status="rejected",reason="")
                if isinstance(target,str) or solver_error:
                    record["reason"] = target if isinstance(target,str) else solver_error
                else:
                    try:
                        arrays,prediction,metrics = solve_mapping(F,target,*solver)
                        record.update(metrics)
                        reasons = check_requirements(metrics,args.required_rank,args.max_fit_abs,args.max_mass_error,args.max_offset)
                        record.update(time_alignment=str(target["time_alignment"]),target_normalized=bool(target["target_normalized"]),
                                      original_mass_min=float(target["original_mass_min"]),original_mass_max=float(target["original_mass_max"]))
                        if reasons:
                            record["reason"] = "；".join(reasons)
                        else:
                            folder = output/"models"/lc_id/target_id
                            prediction_folder = output/"predictions"/lc_id/target_id
                            folder.mkdir(parents=True,exist_ok=True)
                            prediction_folder.mkdir(parents=True,exist_ok=True)
                            metadata = {key:target[key] for key in ("image_shape","model_time","LC_time_s","time_alignment",
                                        "LC_time_is_frame_index","target_normalized","source_kind","target_xy") if key in target}
                            np.savez_compressed(folder/f"A_{video_id}.npz",**arrays,**metadata,lc_id=lc_id,model_id=target_id,video_id=video_id)
                            np.savez_compressed(prediction_folder/f"{video_id}.npz",probability=prediction,
                                                active_cells=target["active"],cell_area=target["cell_area"],**metadata)
                            record["status"] = "accepted"
                    except (ValueError,ArithmeticError,np.linalg.LinAlgError) as error:
                        record["reason"] = str(error)
                # --overwrite时清理这次已拒绝的同名旧结果，避免误用上次合格A。
                if record["status"] == "rejected" and args.overwrite:
                    (output/"models"/lc_id/target_id/f"A_{video_id}.npz").unlink(missing_ok=True)
                    (output/"predictions"/lc_id/target_id/f"{video_id}.npz").unlink(missing_ok=True)
                records.append(record)
                print(f"{lc_id}/{video_id} × {target_id}: {record['status']} {record['reason']}",flush=True)
    fields = list(dict.fromkeys(key for record in records for key in record))
    with (output/"metrics.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    accepted = [record for record in records if record["status"] == "accepted"]
    summary = dict(pairs=len(records),accepted=len(accepted),rejected=len(records)-len(accepted),
                   requirements=dict(required_rank=args.required_rank,max_fit_abs=args.max_fit_abs,
                                     max_mass_error=args.max_mass_error,max_offset=args.max_offset,minimum_probability=0),
                   formula="Phi=F.T; Y=P.T; A0=Y@pinv(Phi); P=Ut@L0.T; A=L@R after numerical guard",
                   history_lags=[0,1,2],scope="逐G逐P训练内插值；无共享A、岭回归、零空间升秩",
                   lc_sources=[str(path.resolve()) for path in args.lc_files],target_sources=[str(path.resolve()) for path in args.target_files],
                   accepted_matrices=[dict(lc_id=r["lc_id"],model_id=r["model_id"],video_id=r["video_id"],A_rank=r["A_rank"]) for r in accepted])
    write_json(output/"summary.json",summary)
    print(f"完成：合格{len(accepted)}，拒绝{len(records)-len(accepted)}；详情见{output/'metrics.csv'}")


if __name__ == "__main__":
    main()
