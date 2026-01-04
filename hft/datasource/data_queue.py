import time
import bisect
import numpy as np


class TickHistory:
    def __init__(self):
        self.history = []
        self.time_difference = []

    def __getstate__(self) -> dict:
        """Support pickle serialization."""
        return {
            'history': self.history,
            'time_difference': self.time_difference,
        }

    def __setstate__(self, state: dict):
        """Support pickle deserialization."""
        self.history = state.get('history', [])
        self.time_difference = state.get('time_difference', [])

    def append(self, timestamp, value):
        pos = len(self.history)
        for i in range(len(self.history) - 1, -1, -1):
            if abs(self.history[i][0] - timestamp) < 1e-3:
                return  # ignore duplicate
            elif self.history[i][0] > timestamp:
                pos = i
            else:
                break
        self.history.insert(pos, (timestamp, value))
        self.time_difference.insert(pos, timestamp - time.time())

    @property
    def time_diff_mean(self):
        if len(self.time_difference) > 0:
            return np.mean(self.time_difference)
        return 0.0

    @property
    def current_time(self):
        return time.time() + self.time_diff_mean

    @property
    def best_time(self):
        if len(self.history) == 0:
            return self.current_time
        return self.history[-1][0]

    def shrink(self, before_timestamp):
        start = bisect.bisect_left(self.history, before_timestamp, key=lambda x: x[0])
        if start > 0:
            self.history = self.history[start:]
            self.time_difference = self.time_difference[start:]

    def get_cv(self, start_timestamp, end_timestamp, min_points=3) -> float | None:
        start_pos = bisect.bisect_left(self.history, start_timestamp, key=lambda x: x[0])
        end_pos = bisect.bisect_right(self.history, end_timestamp, key=lambda x: x[0])
        history = self.history[start_pos:end_pos]
        if len(history) < min_points:
            return None
        times = np.array([h[0] for h in history], dtype=float)
        dtimes = np.diff(times)
        m = abs(dtimes.mean())
        if m < 1e-8:
            return None
        cv = dtimes.std() / m
        return cv

    def get_range(self, start_timestamp, end_timestamp, min_points=3) -> float | None:
        start_pos = bisect.bisect_left(self.history, start_timestamp, key=lambda x: x[0])
        end_pos = bisect.bisect_right(self.history, end_timestamp, key=lambda x: x[0])
        history = self.history[start_pos:end_pos]
        if len(history) < min_points:
            return None
        return abs(history[-1][0] - history[0][0]) / abs(end_timestamp - start_timestamp)

    def get_interpolate(self, timestamps):
        history = np.array(self.history, dtype=float)
        return np.interp(timestamps, history[:, 0], history[:, 1])

    def is_healthy(self, tolerance_seconds=5.0) -> bool:
        if len(self.history) == 0:
            return False
        return self.current_time - self.history[-1][0] < tolerance_seconds
