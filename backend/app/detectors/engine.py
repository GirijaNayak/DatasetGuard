"""Real, deterministic quality checks for tabular data."""
from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

import numpy as np
import pandas as pd
from scipy.spatial.distance import jensenshannon
from scipy.stats import chi2_contingency, ks_2samp
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nan", "missing", "?", "-"}


def finding(category: str, title: str, description: str, columns: list[str], rows: list[int], severity: str,
            confidence: float, method: str, evidence: dict[str, Any], recommendation: str, uncertainty: str = "") -> dict[str, Any]:
    return {"id": f"{category}-{len(rows)}-{abs(hash(title)) % 100000:05d}", "category": category, "title": title,
            "description": description, "columns": columns, "rows": rows[:1000], "affected_records": len(rows),
            "severity": severity, "confidence": round(float(confidence), 2), "method": method,
            "evidence": evidence, "recommendation": recommendation, "uncertainty": uncertainty}


def analyze(df: pd.DataFrame, target: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    n = max(len(df), 1)
    # Nulls and common textual missing markers
    for col in df.columns:
        series = df[col]
        textual = series.astype("string").str.strip().str.lower().isin(MISSING_TOKENS) & series.notna()
        mask = series.isna() | textual
        count = int(mask.sum())
        if count:
            pct = count / n * 100
            out.append(finding("missing", f"Missing values in {col}", f"{count:,} records ({pct:.1f}%) have missing values in this column.", [str(col)],
                np.flatnonzero(mask.to_numpy()).tolist(), "high" if pct >= 20 else "medium" if pct >= 5 else "low", .99,
                "Null check and common missing-token scan", {"count": count, "percentage": round(pct, 2), "tokens": sorted(MISSING_TOKENS)},
                "Review missingness, then choose median/mean imputation for numeric data or mode/explicit category for categorical data. Rows are never dropped automatically."))
        if series.isna().all():
            out.append(finding("missing", f"Entirely empty column: {col}", "No records contain a value in this column.", [str(col)], list(range(len(df))), "high", 1,
                "Column completeness", {"non_missing": 0}, "Remove this column only if it is not required by your workflow."))

    dup = df.duplicated(keep="first")
    if dup.any():
        ids = np.flatnonzero(dup.to_numpy()).tolist()
        out.append(finding("duplicates", "Exact duplicate records", f"{len(ids):,} rows repeat an earlier complete record.", list(map(str, df.columns)), ids,
            "high" if len(ids) / n >= .1 else "medium", .99, "Pandas exact row equality", {"duplicate_rows": len(ids), "groups": int(df[dup].drop_duplicates().shape[0])},
            "Inspect the repeated records and remove duplicates if they are accidental."))

    # Univariate robust IQR anomalies; reported as unusual values, never errors.
    numeric = df.select_dtypes(include="number")
    for col in numeric.columns:
        s = numeric[col].replace([np.inf, -np.inf], np.nan).dropna()
        if len(s) < 8 or s.nunique() < 4: continue
        q1, q3 = s.quantile([.25, .75]); iqr = q3-q1
        if not np.isfinite(iqr) or iqr == 0: continue
        mask = (df[col] < q1-1.5*iqr) | (df[col] > q3+1.5*iqr)
        ids = np.flatnonzero(mask.fillna(False).to_numpy()).tolist()
        if ids:
            out.append(finding("outliers", f"Unusual values in {col}", f"{len(ids):,} records fall outside the 1.5×IQR fences.", [str(col)], ids,
                "medium" if len(ids)/n > .05 else "low", .85, "Tukey IQR fences", {"q1": float(q1), "q3": float(q3), "lower_fence": float(q1-1.5*iqr), "upper_fence": float(q3+1.5*iqr), "count": len(ids)},
                "Inspect these values in context. Statistical outliers are not automatically data errors.", "IQR fences are distribution-sensitive and may flag valid rare observations."))

    # Whitespace and mixed-type consistency
    for col in df.columns:
        s = df[col].dropna().astype(str)
        if len(s) == 0: continue
        trim_mask = df[col].notna() & (df[col].astype(str) != df[col].astype(str).str.strip())
        ids = np.flatnonzero(trim_mask.to_numpy()).tolist()
        if ids:
            out.append(finding("consistency", f"Leading or trailing spaces in {col}", f"{len(ids):,} values contain surrounding whitespace.", [str(col)], ids, "low", .98,
                "String boundary whitespace check", {"count": len(ids)}, "Trim surrounding whitespace after confirming it is not meaningful."))
        if not pd.api.types.is_numeric_dtype(df[col]) and s.nunique() <= 100:
            norm: dict[str, set[str]] = {}
            for value in s.unique(): norm.setdefault(value.strip().casefold(), set()).add(value)
            groups = [sorted(v) for v in norm.values() if len(v) > 1]
            if groups:
                affected = s.isin({v for g in groups for v in g})
                ids = np.flatnonzero((df[col].notna() & df[col].astype(str).isin({v for g in groups for v in g})).to_numpy()).tolist()
                out.append(finding("consistency", f"Category variants in {col}", f"Found {len(groups)} groups that differ only by case or surrounding spaces.", [str(col)], ids,
                    "low", .9, "Case-folded and trimmed category comparison", {"variants": groups[:20]}, "Review category spellings and normalize equivalent labels if they represent the same value."))

    # Multivariate Isolation Forest on numeric columns, bounded cost
    if numeric.shape[1] >= 2 and len(df) >= 30:
        data = numeric.replace([np.inf, -np.inf], np.nan)
        med = data.median().fillna(0); x = data.fillna(med).to_numpy()
        if x.shape[1] > 30: x = x[:, :30]; cols = list(map(str, numeric.columns[:30]))
        else: cols = list(map(str, numeric.columns))
        if len(x) > 50000:
            x = x[:50000]
        if np.any(np.nanstd(x, axis=0) > 0):
            try:
                z = StandardScaler().fit_transform(x)
                model = IsolationForest(contamination="auto", random_state=17, n_estimators=100, n_jobs=1)
                pred = model.fit_predict(z); scores = -model.score_samples(z); threshold = float(np.quantile(scores, .99))
                ids = np.flatnonzero(pred == -1).tolist()
                if ids:
                    out.append(finding("outliers", "Multivariate anomaly candidates", f"Isolation Forest flagged {len(ids):,} records based on combinations of numeric features.", cols, ids,
                        "medium", .72, "Isolation Forest (100 trees; fixed seed)", {"count": len(ids), "sample_scores": [round(float(scores[i]), 4) for i in ids[:20]], "review_threshold": threshold},
                        "Review the records across the listed features. Anomaly scores indicate rarity, not incorrectness.", "Unsupervised model flags are exploratory; the contamination rate is inferred by the model."))
            except (ValueError, FloatingPointError): pass

    # Correlation pairs, bounded to numeric columns
    if numeric.shape[1] >= 2:
        corr = numeric.corr(method="pearson", min_periods=3)
        for i, c1 in enumerate(corr.columns):
            for c2 in corr.columns[i+1:]:
                value = corr.loc[c1, c2]
                if pd.notna(value) and abs(value) >= .95:
                    out.append(finding("correlation", f"Highly correlated features: {c1} and {c2}", f"Pearson correlation is {value:.3f}.", [str(c1), str(c2)], [], "low", .95,
                        "Pearson correlation (|r| ≥ 0.95)", {"pearson_r": round(float(value), 4)}, "Check for redundant features, duplicated calculations, or a valid domain relationship.", "High correlation alone is not evidence of leakage or a data error."))

    if target and target in df.columns:
        y = df[target].dropna(); counts = y.value_counts()
        if len(counts) > 1:
            ratio = float(counts.iloc[0] / max(counts.iloc[-1], 1))
            if ratio >= 2:
                out.append(finding("imbalance", f"Class imbalance in {target}", f"Largest class is {ratio:.1f}× the smallest class.", [str(target)], [],
                    "medium" if ratio < 10 else "high", .99, "Target class frequency ratio", {"counts": {str(k): int(v) for k,v in counts.items()}, "ratio": round(ratio, 2)},
                    "Consider stratified splits, class weights, or resampling within the training fold. Do not resample before splitting."))
            rare = counts[counts <= max(2, int(n * .01))]
            if len(rare):
                mask = df[target].isin(rare.index)
                ids = np.flatnonzero(mask.to_numpy()).tolist()
                out.append(finding("label_quality", f"Rare labels in {target}", f"{len(ids):,} records belong to classes with very few examples.", [str(target)], ids,
                    "low", .55, "Target class frequency scan", {"rare_class_counts": {str(k): int(v) for k,v in rare.items()}},
                    "Review these labels and their supporting features. Rare classes may be valid and should not be removed solely due to low frequency.", "Rarity is a review cue, not evidence that a label is incorrect."))
            if pd.api.types.is_numeric_dtype(y) and len(y) >= 10:
                for col in numeric.columns:
                    if col == target: continue
                    pair = pd.concat([pd.to_numeric(df[col], errors="coerce"), pd.to_numeric(df[target], errors="coerce")], axis=1).dropna()
                    if len(pair) >= 10 and pair.iloc[:,0].nunique() > 1 and pair.iloc[:,1].nunique() > 1:
                        corr_value = float(pair.corr(method="pearson").iloc[0,1])
                        if abs(corr_value) >= .98:
                            out.append(finding("leakage", f"Feature closely tracks target: {col}", f"Pearson correlation with {target} is {corr_value:.3f}.", [str(col), str(target)], [],
                                "medium", .75, "Target-feature Pearson correlation scan (|r| ≥ 0.98)", {"pearson_r": round(corr_value,4), "paired_rows": len(pair)},
                                "Check whether this feature is computed from the target or becomes available only after the prediction time.", "A strong relationship can be legitimate and does not establish leakage."))
            elif y.nunique() == 2 and len(y) >= 30:
                labels = y.astype(str)
                for col in numeric.columns:
                    if col == target: continue
                    pair = pd.DataFrame({"x": pd.to_numeric(df[col], errors="coerce"), "y": labels}).dropna()
                    if len(pair) < 30 or pair["x"].nunique() < 3: continue
                    x=pair["x"].to_numpy(); yy=pair["y"].to_numpy(); classes=np.unique(yy)
                    thresholds=np.unique(np.quantile(x,np.linspace(.02,.98,49)))
                    best=(0.0,None,None)
                    for threshold in thresholds:
                        high=x>threshold
                        for positive_side in (True,False):
                            predicted=np.where(high == positive_side,classes[1],classes[0])
                            accuracy=float(np.mean(predicted==yy))
                            if accuracy>best[0]: best=(accuracy,float(threshold),positive_side)
                    if best[0] >= .95:
                        out.append(finding("leakage", f"Feature nearly separates target: {col}", f"A single threshold on {col} separates the two {target} classes with {best[0]:.1%} accuracy.", [str(col), str(target)], [],
                            "medium", .7, "Single-feature target separation threshold scan", {"threshold_accuracy": round(best[0],4), "threshold": best[1], "paired_rows": len(pair)},
                            "Check feature provenance, split construction, and prediction-time availability before model training.", "This in-sample threshold scan is exploratory; it does not confirm leakage."))
            if len(y) >= 30:
                for col in df.columns:
                    if col == target: continue
                    x = df[col]
                    paired = pd.DataFrame({"x": x, "y": df[target]}).dropna()
                    if paired["x"].nunique() < 2 or paired["x"].nunique() > min(50, int(len(paired)*.5)): continue
                    purity = float(paired.groupby("x", dropna=False)["y"].agg(lambda z: z.value_counts().max()/len(z)).mean())
                    if purity >= .995:
                        out.append(finding("leakage", f"Feature nearly determines target: {col}", f"Values in {col} map almost one-to-one to {target} in this dataset (average within-value purity {purity:.3f}).", [str(col), str(target)], [],
                            "medium", .65, "Categorical feature-to-target purity scan", {"purity": round(purity,4), "paired_rows": len(paired)},
                            "Inspect feature provenance and prediction-time availability. It may be a proxy, direct encoding, or a valid deterministic relationship.", "This association scan is not a causal leakage test."))

    return out


def compare_datasets(reference: pd.DataFrame, comparison: pd.DataFrame) -> dict[str, Any]:
    shared = [c for c in reference.columns if c in comparison.columns]
    results = []
    for col in shared:
        a, b = reference[col], comparison[col]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            av = pd.to_numeric(a, errors="coerce").dropna().to_numpy(); bv = pd.to_numeric(b, errors="coerce").dropna().to_numpy()
            if not len(av) or not len(bv): continue
            stat, pvalue = ks_2samp(av, bv)
            cuts = np.unique(np.quantile(av, np.linspace(0,1,11)))
            if len(cuts) < 3: psi = 0.0
            else:
                bins_a = np.histogram(av, bins=np.r_[-np.inf,cuts[1:-1],np.inf])[0].astype(float)
                bins_b = np.histogram(bv, bins=np.r_[-np.inf,cuts[1:-1],np.inf])[0].astype(float)
                pa=(bins_a+.5)/(bins_a.sum()+.5*len(bins_a)); pb=(bins_b+.5)/(bins_b.sum()+.5*len(bins_b))
                psi=float(np.sum((pb-pa)*np.log(pb/pa)))
            results.append({"column":str(col),"kind":"numeric","test":"Kolmogorov-Smirnov","statistic":round(float(stat),4),"p_value":float(pvalue),"effect_size":"KS distance","effect":round(float(stat),4),"psi":round(psi,4),"reference_rows":len(av),"comparison_rows":len(bv),"changed":bool(pvalue < .05 and stat >= .1)})
        else:
            ca=a.astype("string").fillna("<missing>").value_counts(); cb=b.astype("string").fillna("<missing>").value_counts(); cats=sorted(set(ca.index)|set(cb.index))
            va=np.array([ca.get(k,0) for k in cats],dtype=float); vb=np.array([cb.get(k,0) for k in cats],dtype=float)
            distance=float(jensenshannon(va+.5,vb+.5,base=2) ** 2)
            table=np.vstack([va,vb])
            try: _,pvalue,_,_=chi2_contingency(table, correction=False); pvalue=float(pvalue)
            except ValueError: pvalue=1.0
            results.append({"column":str(col),"kind":"categorical","test":"Chi-square + Jensen-Shannon","statistic":round(distance,4),"p_value":pvalue,"effect_size":"Jensen-Shannon divergence","effect":round(distance,4),"psi":None,"reference_rows":int(va.sum()),"comparison_rows":int(vb.sum()),"changed":bool(pvalue < .05 and distance >= .05)})
    # Exact records shared across the common schema
    numeric_columns: set[str] = set()
    for col in shared:
        left=pd.to_numeric(reference[col],errors="coerce"); right=pd.to_numeric(comparison[col],errors="coerce")
        if left.notna().sum()==reference[col].notna().sum() and right.notna().sum()==comparison[col].notna().sum(): numeric_columns.add(col)
    def keys(df: pd.DataFrame) -> Counter:
        if not shared: return Counter()
        normalized=[]
        for col in shared:
            if col in numeric_columns:
                series=pd.to_numeric(df[col],errors="coerce")
                values=[]
                for value in series:
                    if pd.isna(value): values.append(None)
                    else:
                        try: values.append(Decimal(str(value)).normalize())
                        except InvalidOperation: values.append(str(value))
            else:
                values=[None if pd.isna(value) else str(value) for value in df[col]]
            normalized.append(values)
        return Counter(zip(*normalized))
    overlap=sum((keys(reference)&keys(comparison)).values())
    return {"shared_columns":len(shared),"columns":results,"exact_shared_records":overlap,"reference_rows":len(reference),"comparison_rows":len(comparison),
            "note":"Statistical significance depends on sample size. Effect sizes are shown alongside p-values; a detected shift is not automatically a quality defect."}


def profile(df: pd.DataFrame) -> dict[str, Any]:
    columns = []
    for c in df.columns:
        s = df[c]
        token_mask = s.astype("string").str.strip().str.lower().isin(MISSING_TOKENS)
        nulls = int((s.isna() | token_mask).sum()); unique = int(s.nunique(dropna=True))
        item: dict[str, Any] = {"name": str(c), "dtype": str(s.dtype), "kind": "numeric" if pd.api.types.is_numeric_dtype(s) else "categorical",
            "missing": nulls, "missing_pct": round(nulls/max(len(df),1)*100,2), "unique": unique, "unique_pct": round(unique/max(len(df),1)*100,2)}
        if pd.api.types.is_numeric_dtype(s):
            d = s.describe(percentiles=[.25,.5,.75]).replace({np.nan: None}).to_dict()
            item["stats"] = {str(k): (float(v) if v is not None and np.isfinite(v) else None) for k,v in d.items()}
            item["mean"] = float(s.mean()) if s.notna().any() else None; item["median"] = float(s.median()) if s.notna().any() else None
            item["skew"] = float(s.skew()) if s.count() > 2 and np.isfinite(s.skew()) else None
        else:
            item["top_values"] = [{"value": str(k), "count": int(v)} for k,v in s.astype("string").value_counts(dropna=False).head(8).items()]
        columns.append(item)
    corr = df.select_dtypes(include="number").corr().replace({np.nan: None}).to_dict()
    return {"rows": int(len(df)), "columns": int(len(df.columns)), "missing": sum(c["missing"] for c in columns), "duplicate_rows": int(df.duplicated().sum()),
            "numeric_columns": int(len(df.select_dtypes(include="number").columns)), "categorical_columns": int(len(df.columns)-len(df.select_dtypes(include="number").columns)),
            "column_profiles": columns, "correlation": corr}
