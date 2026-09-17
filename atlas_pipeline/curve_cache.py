"""Bounded numerical probability cache; no images or altered/sampled populations."""

from collections import OrderedDict
import threading

import numpy as np
import pandas as pd


class ProbabilityCurveCache:
    def __init__(self, max_bytes=64 * 1024 * 1024):
        self.max_bytes = max_bytes
        self._entries = OrderedDict()
        self._lock = threading.Lock()
        self.bytes = self.hits = self.computations = 0

    def clear(self):
        with self._lock:
            self._entries.clear()
            self.bytes = self.hits = self.computations = 0

    def curve(self, key, columns):
        with self._lock:
            if key in self._entries:
                self.hits += 1
                self._entries.move_to_end(key)
                return self._entries[key]
            arrays = []
            for column in columns:
                numeric = pd.to_numeric(column, errors="coerce").to_numpy(dtype=float)
                arrays.append(numeric[np.isfinite(numeric)])
            values = np.sort(np.concatenate(arrays)) if arrays else np.array([], dtype=float)
            count = len(values)
            probabilities = (np.arange(1, count + 1) - 0.3) / (count + 0.4)
            values.setflags(write=False)
            probabilities.setflags(write=False)
            result = (values, probabilities)
            self.computations += 1
            size = values.nbytes + probabilities.nbytes
            if size <= self.max_bytes:
                self._entries[key] = result
                self.bytes += size
                while self.bytes > self.max_bytes:
                    _, removed = self._entries.popitem(last=False)
                    self.bytes -= sum(array.nbytes for array in removed)
            return result


PROBABILITY_CURVES = ProbabilityCurveCache()
