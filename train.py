"""Train a model to predict whether price HOLDS or BREAKS a volume-profile level.

Pipeline:
  raw OHLCV  ->  dataset.build_dataset (shape + level features, hold/break label)
             ->  sklearn classifier (logistic baseline + gradient boosting)
             ->  metrics, feature importance, hold-rate-by-shape sanity check

Run:
  python train.py                    # synthetic data (works out of the box)
  python train.py a.txt b.txt ...    # one or more intraday OHLCV files (UTC)

The model and the column layout are saved to `vp_model.joblib` so they can be
reused for scoring later.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import data as data_mod
import sessions
from dataset import build_dataset
from shapes import SHAPE_BIAS

MODEL_PATH = "vp_model.joblib"

# Categorical columns get one-hot encoded; the rest are used as-is.
CAT_COLS = ["timeframe", "level_name", "shape"]
NUM_COLS = [
    "poc_pos", "va_pos", "va_width", "skew", "n_modes",
    "valley_ratio", "concentration", "from_below", "confluent",
]


def load_all(paths: list[str]) -> pd.DataFrame:
    """Build a pooled dataset across all input files (or synthetic if none)."""
    frames: list[pd.DataFrame] = []
    if not paths:
        print("no CSV given — using synthetic intraday data")
        bars_et = sessions.assume_eastern(data_mod.synthetic(days=400))
        frames.append(build_dataset(bars_et))
    else:
        for path in paths:
            name = path.split("/")[-1]
            bars_et = sessions.to_eastern(data_mod.load_csv(path), "UTC")
            df = build_dataset(bars_et)
            df["source"] = name
            print(f"{name}: {len(df)} labeled level tests")
            frames.append(df)
    return pd.concat(frames, ignore_index=True)


def encode(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """One-hot the categoricals; return (feature matrix, column order)."""
    X = pd.get_dummies(df[CAT_COLS + NUM_COLS], columns=CAT_COLS, dtype=float)
    return X, list(X.columns)


def hold_rate_by_shape(df: pd.DataFrame) -> None:
    print("\n=== Hold rate by profile shape (the signal the model learns) ===")
    base = df["label"].mean()
    print(f"  baseline hold rate (all):  {base:5.1%}  ({len(df)} tests)")
    for shape, grp in df.groupby("shape"):
        if len(grp) >= 5:
            print(f"  {shape:5s} {SHAPE_BIAS.get(shape, ''):40s} "
                  f"{grp['label'].mean():5.1%}  ({len(grp)} tests)")


def _split(df: pd.DataFrame, X: np.ndarray, y: np.ndarray, test_size: float):
    """Time-ordered (walk-forward) split: train on the past, test on the future.

    Financial series leak future into past under a random split, which flatters
    the score. Sorting by the tested session and cutting at a date is the honest
    evaluation. Falls back to a random split if no session timestamp is present.
    """
    if "session" not in df.columns:
        from sklearn.model_selection import train_test_split
        return (*train_test_split(X, y, test_size=test_size, random_state=7,
                                  stratify=y), "random")
    order = np.argsort(df["session"].values, kind="stable")
    cut = int(len(order) * (1 - test_size))
    tr, te = order[:cut], order[cut:]
    return X[tr], X[te], y[tr], y[te], "walk-forward"


def train(df: pd.DataFrame, test_size: float = 0.25):
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, roc_auc_score

    X, cols = encode(df)
    Xv = X.values
    y = df["label"].values

    if y.sum() in (0, len(y)):
        print("\nonly one class present — cannot train a classifier.")
        return None, cols

    X_tr, X_te, y_tr, y_te, split_kind = _split(df, Xv, y, test_size)
    if y_te.sum() in (0, len(y_te)):
        print(f"\n{split_kind} test fold has one class only — skipping.")
        return None, cols

    # Majority-class baseline: how good is "always predict the common outcome"?
    majority = int(round(y_tr.mean()))
    base_acc = accuracy_score(y_te, np.full_like(y_te, majority))

    models = {
        "logistic": LogisticRegression(max_iter=1000, class_weight="balanced"),
        "gboost": GradientBoostingClassifier(random_state=7),
    }
    print(f"\n=== Model performance ({split_kind} split, "
          f"{len(y_te)} test / {len(y_tr)} train) ===")
    print(f"  {'majority baseline':22s} acc={base_acc:5.1%}")
    best, best_auc = None, -1.0
    for name, model in models.items():
        model.fit(X_tr, y_tr)
        proba = model.predict_proba(X_te)[:, 1]
        acc = accuracy_score(y_te, model.predict(X_te))
        auc = roc_auc_score(y_te, proba)
        print(f"  {name:22s} acc={acc:5.1%}  auc={auc:.3f}")
        if auc > best_auc:
            best, best_auc = model, auc

    _walk_forward_cv(df, Xv, y)
    _feature_importance(best, cols)
    return best, cols


def _walk_forward_cv(df: pd.DataFrame, X: np.ndarray, y: np.ndarray,
                     n_splits: int = 5) -> None:
    """Expanding-window walk-forward CV — a less noisy estimate than one fold."""
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.model_selection import TimeSeriesSplit

    if "session" in df.columns:
        order = np.argsort(df["session"].values, kind="stable")
        X, y = X[order], y[order]
    if len(y) < (n_splits + 1) * 10:
        n_splits = max(2, len(y) // 20)

    accs, aucs = [], []
    for tr, te in TimeSeriesSplit(n_splits=n_splits).split(X):
        if y[tr].sum() in (0, len(tr)) or y[te].sum() in (0, len(te)):
            continue
        m = GradientBoostingClassifier(random_state=7).fit(X[tr], y[tr])
        accs.append(accuracy_score(y[te], m.predict(X[te])))
        aucs.append(roc_auc_score(y[te], m.predict_proba(X[te])[:, 1]))
    if aucs:
        print(f"\n=== Walk-forward CV (gboost, {len(aucs)} folds) ===")
        print(f"  acc {np.mean(accs):5.1%} ± {np.std(accs):4.1%}   "
              f"auc {np.mean(aucs):.3f} ± {np.std(aucs):.3f}")


def _feature_importance(model, cols: list[str], top: int = 10) -> None:
    if hasattr(model, "feature_importances_"):
        imp = model.feature_importances_
    elif hasattr(model, "coef_"):
        imp = np.abs(model.coef_[0])
    else:
        return
    order = np.argsort(imp)[::-1][:top]
    print("\n=== Top features ===")
    for i in order:
        print(f"  {cols[i]:28s} {imp[i]:.4f}")


def main(argv: list[str]) -> None:
    df = load_all(argv[1:])
    print(f"\npooled dataset: {len(df)} labeled level tests")
    if df.empty:
        print("no labeled tests produced — need more/finer data.")
        return
    hold_rate_by_shape(df)
    model, cols = train(df)
    if model is not None:
        try:
            import joblib
            joblib.dump({"model": model, "columns": cols}, MODEL_PATH)
            print(f"\nsaved model -> {MODEL_PATH}")
        except Exception as e:  # joblib optional
            print(f"\n(model not saved: {e})")


if __name__ == "__main__":
    main(sys.argv)
