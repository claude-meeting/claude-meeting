import numpy as np


def array(x):
    return np.asarray(x, dtype=np.float32)


def get_peak_memory():
    return 3 * 2**30


def get_active_memory():
    return 2 * 2**30


def get_cache_memory():
    return 2**29
