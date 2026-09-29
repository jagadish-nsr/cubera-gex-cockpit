"""Hand-checks for app/preempt_study.py — the statistics on controlled synthetic inputs."""
import numpy as np

from app.preempt_study import _spearman, _residualise, partial_ic, _verdict


def test_spearman_monotone():
    x = np.arange(20.0)
    assert _spearman(x, 2 * x + 1) == 1.0                     # perfect monotone → +1
    assert _spearman(x, -x) == -1.0
    assert np.isnan(_spearman(np.ones(20), x))                # zero-variance → NaN


def test_residualise_removes_linear_control():
    rng = np.random.default_rng(0)
    c = rng.normal(size=200)                                  # the control (trailing return)
    noise = rng.normal(size=200) * 0.01
    y = 3.0 * c + noise                                       # y is almost pure control
    r = _residualise(y, c)
    assert abs(np.corrcoef(r, c)[0, 1]) < 1e-6                # residual is orthogonal to the control


def test_partial_ic_kills_a_pure_momentum_proxy():
    """If both signal and forward return are just the control (momentum) plus noise, the RAW IC is high
    but the PARTIAL IC (removing the control) collapses to ~0 — the confound the study is built to catch."""
    rng = np.random.default_rng(1)
    n = 300
    ctrl = rng.normal(size=n)
    sig = ctrl + 0.05 * rng.normal(size=n)                    # signal ≈ momentum
    fwd = ctrl + 0.05 * rng.normal(size=n)                    # return ≈ momentum
    raw = _spearman(sig, fwd)
    pic, k = partial_ic(sig, fwd, ctrl)
    assert raw > 0.8 and k == n                               # raw looks very predictive
    assert abs(pic) < 0.2                                     # partial collapses → it was just momentum


def test_partial_ic_keeps_a_genuine_signal():
    """A signal that predicts the return ORTHOGONALLY to the control survives the partial."""
    rng = np.random.default_rng(2)
    n = 300
    ctrl = rng.normal(size=n)
    genuine = rng.normal(size=n)
    sig = genuine + 0.5 * ctrl
    fwd = 0.8 * genuine + 0.5 * ctrl + 0.1 * rng.normal(size=n)
    pic, _ = partial_ic(sig, fwd, ctrl)
    assert pic > 0.4                                          # real orthogonal link survives


def test_verdict_rule():
    def cell(ic, ci0, plc):
        return {"partial_ic": ic, "ci_excludes_0": ci0, "beats_placebo": plc}
    confirm = {5: cell(0.2, True, True), 10: cell(0.18, True, True)}
    assert _verdict(confirm) == "CONFIRM"
    # positive but CI includes 0 at h=10 → NULL
    assert _verdict({5: cell(0.2, True, True), 10: cell(0.1, False, True)}) == "NULL"
    # negative with CI excluding 0 at h=5 → KILL
    assert _verdict({5: cell(-0.2, True, True), 10: cell(-0.1, False, False)}) == "KILL"
    assert _verdict({5: cell(0.05, False, False), 10: cell(0.02, False, False)}) == "NULL"
