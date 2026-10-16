"""通用 G(x,y,t) 输入端；保留原16×16均匀空间分箱和三帧历史拼接。

G NPZ：G=(T,N) 或 (V,T,N)，xy=(N,2)，time=(T,)；可选 video_ids。
已分箱 NPZ：mean_green=(T,B) 或 (V,T,B)，可选 time/video_ids。
不改变绿色值尺度，不添加强度归一化，不补零；像素坐标在所有帧中固定。
图像输入：G=(T,H,W) 或 (V,T,H,W)，xy缺省时自动使用像素坐标。
可选固定ROI mask=(H,W)。NPY默认图像格式；目录支持逐帧NPY/PNG/TIF等。
"""
import argparse
import csv
import gzip
import json
import os
import sys
import re
from pathlib import Path

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[variable] = "1"
import numpy as np

BIN_WIDTH = 16


def finite_array(value, name):
    result = np.asarray(value, dtype=np.float64)
    if not np.isfinite(result).all():
        raise ValueError(f"{name} 含 NaN/Inf")
    return result


def validate_time(value, frames, name="time"):
    time = finite_array(value, name)
    if time.shape != (frames,) or (frames > 1 and np.any(np.diff(time) <= 0)):
        raise ValueError(f"{name} 必须为长度{frames}的严格递增时间数组")
    return time


def read_pixel_table(path):
    """原CSV/CSV.GZ宽表；允许任意帧数，仍要求pixel_id,x,y,G_frame_000,...。"""
    path = Path(path)
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig") as stream:
        header = next(csv.reader(stream))
        table = np.loadtxt(stream, delimiter=",", ndmin=2)
    frames = len(header) - 3
    expected = ["pixel_id", "x", "y"] + [f"G_frame_{t:03d}" for t in range(frames)]
    if frames < 1 or header != expected or table.shape[1] != len(header) or len(table) == 0:
        raise ValueError(f"像素表列名/形状不正确：{path}")
    finite_array(table, "pixel table")
    ids = table[:, 0]
    if np.any(ids != ids.astype(np.int64)) or len(np.unique(ids)) != len(ids):
        raise ValueError("pixel_id 必须为不重复的整数")
    return table


def build_geometry(table, bin_width=BIN_WIDTH):
    """与原代码相同：几何中心、floor((rel+width/2)/width)、按(kx,ky)排序。"""
    if not np.isfinite(bin_width) or bin_width <= 0:
        raise ValueError("bin_width 必须为正数，单位与xy一致")
    xy = finite_array(table[:, 1:3], "xy")
    if xy.ndim != 2 or xy.shape[1] != 2 or len(xy) == 0:
        raise ValueError("xy 必须为非空(N,2)")
    if len(np.unique(xy, axis=0)) != len(xy):
        raise ValueError("xy 含重复像素位置；请去重后输入")
    center = xy.mean(axis=0)
    relative_xy = xy - center
    integer_keys = np.floor((relative_xy + bin_width / 2) / bin_width).astype(np.int64)
    keys, membership, counts = np.unique(integer_keys, axis=0, return_inverse=True, return_counts=True)
    return dict(pixel_ids=table[:, 0].astype(np.int64), original_xy=xy, relative_xy=relative_xy,
                center=center, keys=keys, pixel_to_block=membership, counts=counts,
                width=np.array(bin_width))


def geometry_from_xy(xy, bin_width=BIN_WIDTH):
    xy = finite_array(xy, "xy")
    if xy.ndim != 2 or xy.shape[1] != 2 or not len(xy):
        raise ValueError("xy 必须为非空(N,2)")
    return build_geometry(np.column_stack([np.arange(len(xy)), xy]), bin_width)


def validate_geometry(table, geometry):
    if not np.array_equal(table[:, 0], geometry["pixel_ids"]):
        raise ValueError("像素ID或行顺序与固定分箱不一致")
    if not np.array_equal(table[:, 1:3], geometry["original_xy"]):
        raise ValueError("像素坐标与固定分箱不一致")


def bin_samples(G, geometry):
    """(T,N) -> (T,B)，保留bincount求和/实际有效像素数的原计算顺序。"""
    G = finite_array(G, "G")
    membership, counts = geometry["pixel_to_block"], geometry["counts"]
    if G.ndim != 2 or G.shape[0] < 1 or G.shape[1] != len(membership):
        raise ValueError("G 必须为(T,N)，N与固定像素网格一致")
    return np.array([np.bincount(membership, weights=row, minlength=len(counts)) / counts for row in G])


def bin_green(table, geometry):
    validate_geometry(table, geometry)
    return bin_samples(table[:, 3:].T, geometry)


def build_features(mean_green):
    data = finite_array(mean_green, "mean_green")
    if data.ndim != 3 or min(data.shape) < 1:
        raise ValueError("mean_green 必须为(V,T,B)，各维非空")
    indices = np.arange(data.shape[1])
    return np.concatenate([data[:, np.maximum(indices - lag, 0)] for lag in (0, 1, 2)], axis=2)


def uniform_weight_csr(geometry):
    membership, counts = geometry["pixel_to_block"], geometry["counts"]
    order = np.argsort(membership, kind="stable")
    return dict(data=1 / counts[membership[order]], indices=order,
                indptr=np.r_[0, np.cumsum(counts)], shape=np.array([len(counts), len(membership)]))


def read_array_input(path, key):
    """NPZ/NPY/逐帧图像目录。目录按文件名中的数字自然排序。"""
    path = Path(path)
    if path.is_dir():
        extensions = {".npy", ".png", ".tif", ".tiff", ".bmp", ".jpg", ".jpeg"}
        files = sorted((p for p in path.iterdir() if p.suffix.lower() in extensions),
                       key=lambda p: tuple((1, int(s)) if s.isdigit() else (0,s.lower())
                                           for s in re.split(r"(\d+)", p.name)))
        if not files:
            raise ValueError(f"没有逐帧图像：{path}")
        images = []
        for file in files:
            if file.suffix.lower() == ".npy":
                frame = np.load(file, allow_pickle=False)
            else:
                from PIL import Image
                with Image.open(file) as image:
                    if image.mode == "P":
                        raise ValueError("调色板图不能作为数值输入；请先导出原始G/P数组")
                    frame = np.asarray(image)
            if key == "G" and frame.ndim == 3 and frame.shape[-1] in (3,4):
                frame = frame[..., 1]
            if frame.ndim != 2:
                raise ValueError(f"{file}必须为二维数值图；P不能是RGB伪彩图")
            images.append(frame)
        return {key: np.stack(images), "frame_names": np.array([p.name for p in files])}
    if path.suffix.lower() == ".npy":
        return {key: np.load(path, allow_pickle=False)}
    if path.suffix.lower() == ".npz":
        with np.load(path, allow_pickle=False) as source:
            return {name: source[name] for name in source.files}
    raise ValueError("输入须为NPZ、NPY、逐帧图像目录或G的CSV像素宽表")


def prepare_green_images(G, mask=None, time=None, bin_width=BIN_WIDTH, video_ids=None):
    G = np.asarray(G,dtype=np.float64)
    if G.ndim == 3:
        G = G[None, ...]
    if G.ndim != 4 or min(G.shape) < 1:
        raise ValueError("绿色图像数组G必须为(T,H,W)或(V,T,H,W)")
    height, width = G.shape[-2:]
    mask = np.ones((height,width), dtype=bool) if mask is None else np.asarray(mask)
    if mask.shape != (height,width) or not np.isfinite(mask).all() or not np.isin(mask,[0,1]).all() or not mask.any():
        raise ValueError("ROI mask须为与G图像同尺寸、非空的0/1固定掩膜")
    mask = mask.astype(bool)
    yy,xx = np.indices((height,width))
    xy = np.column_stack([xx[mask], yy[mask]])
    frames = G.shape[1]
    result = prepare_lc(G[:,:,mask], xy, np.arange(frames) if time is None else time,
                        bin_width, video_ids)
    result["time_is_frame_index"] = time is None
    result["image_shape"] = np.array([height,width])
    result["mask"] = mask
    return result


def _video_ids(values, videos):
    ids = np.asarray(values).astype(str)
    if ids.shape != (videos,) or len(np.unique(ids)) != videos:
        raise ValueError("video_ids 必须与视频数量一致且不重复")
    return ids


def prepare_lc(G, xy, time, bin_width=BIN_WIDTH, video_ids=None):
    """内存接口：原始G数组 -> 固定分箱 -> 三帧历史特征。"""
    G = finite_array(G, "G")
    if G.ndim == 2:
        G = G[None, ...]
    if G.ndim != 3 or min(G.shape) < 1:
        raise ValueError("G 必须为(T,N)或(V,T,N)")
    geometry = geometry_from_xy(xy, bin_width)
    binned = np.stack([bin_samples(video, geometry) for video in G])
    time = validate_time(time, G.shape[1])
    ids = _video_ids(video_ids if video_ids is not None else [f"v{i+1:03d}" for i in range(len(G))], len(G))
    return dict(mean_green=binned, F=build_features(binned), time=time, time_is_frame_index=False,
                video_ids=ids, geometry=geometry)


def load_lc(path, bin_width=BIN_WIDTH, mask=None):
    path = Path(path)
    if path.suffix.lower() in (".csv", ".gz") and not path.is_dir():
        if mask is not None:
            raise ValueError("CSV使用有效像素坐标定义ROI，不能另加图像mask")
        table = read_pixel_table(path)
        result = prepare_lc(table[:, 3:].T, table[:, 1:3], np.arange(table.shape[1]-3), bin_width)
        result["geometry"] = build_geometry(table, bin_width)
        result["time_is_frame_index"] = True
        return result
    source = read_array_input(path, "G")
    if "mean_green" in source:
        if mask is not None:
            raise ValueError("已分箱数据不能另加图像mask")
        data = finite_array(source["mean_green"], "mean_green")
        if data.ndim == 2:
            data = data[None, ...]
        F = build_features(data)
        time = validate_time(source["time"] if "time" in source else np.arange(data.shape[1]), data.shape[1])
        ids = _video_ids(source["video_ids"] if "video_ids" in source else [f"v{i+1:03d}" for i in range(len(data))], len(data))
        return dict(mean_green=data, F=F, time=time, time_is_frame_index="time" not in source,
                    video_ids=ids, geometry=None)
    G = source["G"]
    if "xy" not in source:
        return prepare_green_images(G, mask if mask is not None else source.get("mask"),
                                    source.get("time"), bin_width, source.get("video_ids"))
    if mask is not None:
        raise ValueError("像素列表的xy已定义ROI，不能另加图像mask")
    frames = G.shape[-2]
    result = prepare_lc(G, source["xy"], source["time"] if "time" in source else np.arange(frames),
                        bin_width, source.get("video_ids"))
    result["time_is_frame_index"] = "time" not in source
    return result


def save_prepared(folder, result):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    time_metadata = {} if result["time_is_frame_index"] else {"time": result["time"]}
    np.savez_compressed(folder / "binned_green.npz", mean_green=result["mean_green"],
                        video_ids=result["video_ids"], **time_metadata)
    if result["geometry"] is not None:
        np.savez_compressed(folder / "geometry.npz", **result["geometry"])
        np.savez_compressed(folder / "W_uniform_sparse.npz", **uniform_weight_csr(result["geometry"]))
    if "image_shape" in result:
        np.savez_compressed(folder / "image_roi.npz", image_shape=result["image_shape"], mask=result["mask"])
    parameters = dict(videos=len(result["F"]), frames=result["F"].shape[1], blocks=result["mean_green"].shape[2],
                      features=result["F"].shape[2], history_lags=[0,1,2],
                      time_is_frame_index=result["time_is_frame_index"],
                      bin_width=None if result["geometry"] is None else float(result["geometry"]["width"]),
                      edge_rule="有效像素均值，ROI外不补零")
    (folder / "parameters.json").write_text(json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--lc-file", type=Path)
    inputs.add_argument("--input-files", nargs="+", type=Path)
    inputs.add_argument("--input-dir", type=Path, help="兼容vNNN_green_nearest.csv.gz目录")
    parser.add_argument("--lc-mask", type=Path, help="固定ROI的二维0/1 NPY文件")
    parser.add_argument("--videos", nargs="+", type=int, help="--input-dir视频序号")
    parser.add_argument("--bin-width", type=float, default=BIN_WIDTH)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    folder = args.output_root.resolve() / "binning"
    if folder.exists() and any(folder.iterdir()) and not args.overwrite:
        parser.error("分箱输出已存在；请指定新目录或--overwrite")
    try:
        if args.lc_file:
            mask = np.load(args.lc_mask, allow_pickle=False) if args.lc_mask else None
            result = load_lc(args.lc_file, args.bin_width, mask)
        else:
            if args.lc_mask:
                raise ValueError("--lc-mask只能与--lc-file图像输入配合使用")
            paths = args.input_files
            if paths is None:
                paths = ([args.input_dir / f"v{v:03d}_green_nearest.csv.gz" for v in sorted(set(args.videos))]
                         if args.videos else sorted(args.input_dir.glob("v*_green_nearest.csv.gz")))
            if not paths:
                raise ValueError("没有发现像素表")
            tables = [read_pixel_table(path) for path in paths]
            geometry = build_geometry(tables[0], args.bin_width)
            binned = np.stack([bin_green(table, geometry) for table in tables])
            result = dict(mean_green=binned, F=build_features(binned), time=np.arange(binned.shape[1]),
                          time_is_frame_index=True, video_ids=np.array([p.name.split('_')[0] for p in paths]), geometry=geometry)
            _video_ids(result["video_ids"], len(paths))
        save_prepared(folder, result)
    except (ValueError, OSError, KeyError, AttributeError) as error:
        parser.error(str(error))
    print(f"完成：{result['F'].shape}（视频数,帧数,特征数）；{folder / 'binned_green.npz'}")


if __name__ == "__main__":
    main()
