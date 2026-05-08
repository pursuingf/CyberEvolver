def magic_denoise(X, knn=5, t=3, n_pca=100, solver="approximate", decay=1, knn_max=None, random_state=None, n_jobs=1, verbose=False):
    import numpy as np
    import graphtools
    import scprep

    if knn_max is None:
        knn_max = knn * 3

    X_work = scprep.utils.toarray(X).astype(np.float64)
    X_work = np.sqrt(X_work)
    X_work, libsize = scprep.normalize.library_size_normalize(X_work, rescale=1, return_library_size=True)

    graph = graphtools.Graph(
        X_work,
        n_pca=n_pca if X_work.shape[1] > n_pca else None,
        knn=knn,
        knn_max=knn_max,
        decay=decay,
        thresh=1e-4,
        random_state=random_state,
        n_jobs=n_jobs,
        verbose=0,
    )

    diff_op = graph.diff_op

    if solver == "approximate":
        data = graph.data_nu
    else:
        data = scprep.utils.to_array_or_spmatrix(graph.data)

    if verbose:
        print(f"    [magic_denoise] data shape: {data.shape}, sum: {data.sum():.6f}")
        print(f"    [magic_denoise] diff_op sum: {diff_op.sum():.6f}")

    data_imputed = scprep.utils.toarray(data)

    if t > 0 and diff_op.shape[1] < data_imputed.shape[1]:
        diff_op_t = np.linalg.matrix_power(scprep.utils.toarray(diff_op), t)
        data_imputed = diff_op_t.dot(data_imputed)
        if verbose:
            print(f"    [magic_denoise] used matrix_power path")
    else:
        for _ in range(t):
            data_imputed = diff_op.dot(data_imputed)
        if verbose:
            print(f"    [magic_denoise] used iteration path")

    if verbose:
        print(f"    [magic_denoise] after diffusion sum: {data_imputed.sum():.6f}")

    if solver == "approximate":
        data_imputed = graph.inverse_transform(data_imputed, columns=None)
        if verbose:
            print(f"    [magic_denoise] after inverse_transform sum: {data_imputed.sum():.6f}")

    data_imputed = np.square(data_imputed)
    data_imputed = scprep.utils.matrix_vector_elementwise_multiply(data_imputed, libsize, axis=0)

    return data_imputed


import inspect
import numpy as np
import scprep
import anndata
import scanpy as sc
import sklearn.metrics
from molecular_cross_validation.mcv_sweep import poisson_nll_loss


BASELINES = {
    "pancreas": {
        "baseline_mse": 0.304721,
        "baseline_poisson": 0.257575,
        "perfect_mse": 0.000000,
        "perfect_poisson": 0.031739,
    },
    "pbmc": {
        "baseline_mse": 0.270945,
        "baseline_poisson": 0.300447,
        "perfect_mse": 0.000000,
        "perfect_poisson": 0.043569,
    },
    "tabula": {
        "baseline_mse": 0.261763,
        "baseline_poisson": 0.206542,
        "perfect_mse": 0.000000,
        "perfect_poisson": 0.026961,
    },
}


def preprocess(X, normtype="sqrt", reverse_norm_order=True):
    if normtype == "sqrt":
        norm_fn = np.sqrt
        denorm_fn = np.square
    elif normtype == "log":
        norm_fn = np.log1p
        denorm_fn = np.expm1
    else:
        raise ValueError(f"Unknown normtype: {normtype}")

    if reverse_norm_order:
        X = scprep.utils.matrix_transform(X, norm_fn)
        X, libsize = scprep.normalize.library_size_normalize(X, rescale=1, return_library_size=True)
    else:
        X, libsize = scprep.normalize.library_size_normalize(X, rescale=1, return_library_size=True)
        X = scprep.utils.matrix_transform(X, norm_fn)
    return X, libsize, denorm_fn


def postprocess(Y, libsize, denorm_fn):
    Y = scprep.utils.matrix_transform(Y, denorm_fn)
    Y = scprep.utils.matrix_vector_elementwise_multiply(Y, libsize, axis=0)
    return Y


def evaluate_mse(test_data, denoised):
    test_X = scprep.utils.toarray(test_data).copy()
    denoised_X = np.asarray(denoised).copy()

    test_adata = anndata.AnnData(X=test_X)
    denoised_adata = anndata.AnnData(X=denoised_X)

    sc.pp.normalize_total(test_adata, target_sum=10000)
    sc.pp.log1p(test_adata)
    sc.pp.normalize_total(denoised_adata, target_sum=10000)
    sc.pp.log1p(denoised_adata)

    return sklearn.metrics.mean_squared_error(test_adata.X, denoised_adata.X)


def evaluate_poisson(train_data, test_data, denoised):
    test_X = scprep.utils.toarray(test_data)
    denoised_X = np.asarray(denoised).copy()
    
    initial_sum = train_data.sum()
    target_sum = test_X.sum()
    denoised_scaled = denoised_X * target_sum / initial_sum
    
    return poisson_nll_loss(test_X, denoised_scaled)


def normalize_score(score, worst, best):
    if worst == best:
        return 0.0
    return (worst - score) / (worst - best)


def run_denoising_eval(magic_denoise_fn, seed=42):
    import openproblems.data
    openproblems.data.no_cleanup()
    from openproblems.data.pancreas import load_pancreas
    from openproblems.tasks.denoising.datasets.utils import split_data

    adata = load_pancreas(test=False, keep_techs=["inDrop1"])
    adata = split_data(adata, seed=seed)

    X_train = scprep.utils.toarray(adata.obsm["train"])
    X_test = scprep.utils.toarray(adata.obsm["test"])

    Y_denoised = magic_denoise_fn(X_train, random_state=seed)

    if not np.isfinite(Y_denoised).all():
        return (np.inf, np.inf)
    if np.any(Y_denoised < 0):
        return (np.inf, np.inf)
    if Y_denoised.max() > X_train.sum():
        return (np.inf, np.inf)

    mse = evaluate_mse(X_test, Y_denoised)
    poisson = evaluate_poisson(X_train, X_test, Y_denoised)

    return (mse, poisson)


EVALUATE_MSE_FUNC = inspect.getsource(evaluate_mse)
EVALUATE_POISSON_FUNC = inspect.getsource(evaluate_poisson)
MAGIC_FUNC = inspect.getsource(magic_denoise)
