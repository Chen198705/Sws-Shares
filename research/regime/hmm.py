"""2 状态高斯 HMM（k-means 固定发射 + 转移矩阵 EM），用于 regime 识别。"""

import numpy as np
import pandas as pd
from scipy.cluster.vq import kmeans2


def _features(close: pd.Series) -> pd.DataFrame:
    ret = close.pct_change(fill_method=None)
    vol = ret.rolling(20).std() * np.sqrt(252)
    ma_ratio = close / close.rolling(60).mean()
    feat = pd.concat(
        [ret.rename("ret"), vol.rename("vol"), ma_ratio.rename("ma_ratio")],
        axis=1,
    )
    return feat.dropna()


def _logsumexp(a, axis=0):
    m = a.max(axis=axis, keepdims=True)
    with np.errstate(over="ignore", invalid="ignore"):
        res = m.squeeze(axis) + np.log(np.exp(a - m).sum(axis=axis) + 1e-300)
    return np.where(np.isfinite(m.squeeze(axis)), res, -np.inf)


def _fit_transition(X: np.ndarray, means, covs, trans, init, n_iter: int, k: int, tau: float = 0.6):
    """固定发射分布，只迭代转移矩阵与初始分布，避免高斯 HMM 塌缩。"""
    n, d = X.shape

    def loglik(X, means, covs):
        out = np.zeros((n, k))
        for j in range(k):
            diff = X - means[j]
            sign, logdet = np.linalg.slogdet(covs[j])
            if sign <= 0 or not np.isfinite(logdet):
                out[:, j] = -np.inf
                continue
            inv = np.linalg.inv(covs[j])
            out[:, j] = -0.5 * (d * np.log(2 * np.pi) + logdet + np.einsum("ni,ij,nj->n", diff, inv, diff))
        return out

    def fb(logB):
        logA = np.log(trans + 1e-300)
        T = np.full((n, k), -np.inf)
        T[0] = np.log(init + 1e-300) + logB[0]
        for t in range(1, n):
            a = T[t - 1][None, :] + logA
            T[t] = logB[t] + _logsumexp(a, axis=0)
        ll = _logsumexp(T[-1], axis=0)
        B = np.full((n, k), -np.inf)
        B[-1] = 0.0
        for t in range(n - 2, -1, -1):
            a = logA + (B[t + 1] + logB[t + 1])[None, :]
            B[t] = _logsumexp(a, axis=1)
        gamma = T + B - ll
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            gamma = np.exp(gamma - gamma.max(axis=1, keepdims=True))
        gamma = np.nan_to_num(gamma, nan=1.0 / k, posinf=1.0 / k, neginf=0.0)
        gamma /= gamma.sum(axis=1, keepdims=True)
        return ll, gamma

    ll, gamma = None, None
    for it in range(n_iter):
        logB = loglik(X, means, covs)
        if logB is None:
            break
        ll, gamma = fb(logB)
        gsum = gamma.sum(axis=0)
        if gsum.min() < 1e-6:
            break
        with np.errstate(invalid="ignore", over="ignore", divide="ignore"):
            trans = np.nan_to_num(gamma[:-1].T @ gamma[1:], nan=0.0, posinf=0.0, neginf=0.0)
            trans = trans / np.maximum(gsum[:, None] - gamma[-1][:, None], 1e-12)
        trans = np.nan_to_num(trans, nan=0.0, posinf=0.0, neginf=0.0)
        prior = np.array([[0.9, 0.1], [0.1, 0.9]])
        trans = tau * trans + (1 - tau) * prior
        trans = np.clip(trans, 0.05, 0.95)
        trans /= trans.sum(axis=1, keepdims=True)
        init = gamma[0] / gamma[0].sum()

    if gamma is None:
        return None
    return {"ll": ll, "means": means, "covs": covs, "trans": trans, "init": init, "gamma": gamma}


def fit_hmm(close: pd.Series, n_iter: int = 120, restarts: int = 5, seed: int = 42) -> dict:
    """k-means 初始化 + 多重重启的高斯 HMM 拟合。"""
    feat = _features(close)
    if len(feat) < 120:
        return {"states": None, "params": None}
    X = feat.values.astype(float)
    Xs = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-12)
    n, d = Xs.shape
    k = 2
    best = None
    for r in range(restarts):
        try:
            centroids, labels = kmeans2(Xs, k, minit="++", seed=seed + r)
        except Exception:
            rng = np.random.default_rng(seed + r)
            centroids = Xs[rng.choice(n, k, replace=False)]
            labels = np.argmin(((Xs[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2), axis=1)
        if len(set(labels)) < k:
            labels[labels == labels[0]] = 1
        means = centroids.copy()
        covs = np.stack([
            np.diag(np.var(Xs[labels == j], axis=0) + 1e-3) for j in range(k)
        ])
        trans = np.zeros((k, k))
        for t in range(n - 1):
            trans[labels[t], labels[t + 1]] += 1
        trans = np.clip(trans + 1.0, 0.02, None)
        trans /= trans.sum(axis=1, keepdims=True)
        init = np.array([(labels == j).mean() for j in range(k)]) + 1e-6
        init /= init.sum()
        res = _fit_transition(Xs, means, covs, trans, init, n_iter, k)
        share = res["gamma"].sum(axis=0) / n
        if res is not None and share.min() >= 0.05 and share.max() <= 0.95 \
                and (best is None or res["ll"] > best["ll"]):
            best = res
    if best is None:
        return {"states": None, "params": None}

    gamma = best["gamma"]
    states = np.argmax(gamma, axis=1)
    # 0=低收益态，1=高收益态（在标准化空间同样适用）
    if best["means"][0, 0] > best["means"][1, 0]:
        states = 1 - states
        best["means"] = best["means"][::-1].copy()
        best["covs"] = best["covs"][::-1].copy()
    series = pd.Series(states, index=feat.index, name="hmm_state")
    return {
        "states": series,
        "params": {
            "means": best["means"].tolist(),
            "covs": best["covs"].tolist(),
            "trans": best["trans"].tolist(),
            "init": best["init"].tolist(),
            "n_iter": n_iter,
            "restarts": restarts,
            "loglik": float(best["ll"]),
            "state_share": series.value_counts(normalize=True).round(4).to_dict(),
        },
    }


def hmm_label(close: pd.Series) -> pd.Series:
    """返回 {date: 🐂/🐻/🌊/❓} 映射：HMM 高收益态叠加大盘趋势。"""
    fit = fit_hmm(close)
    if fit["states"] is None:
        return pd.Series(index=close.index, dtype=object)
    states = fit["states"]
    ma60 = close.rolling(60).mean()
    labels = {}
    for d, s in states.items():
        cur = close.loc[d]
        ma = ma60.loc[d]
        if s == 1 and cur > ma * 1.02:
            labels[d] = "🐂 牛市"
        elif s == 0 and cur < ma * 0.98:
            labels[d] = "🐻 熊市"
        elif abs(cur / ma - 1) < 0.05:
            labels[d] = "🌊 震荡市"
        else:
            labels[d] = "❓ 转换期"
    return pd.Series(labels)
