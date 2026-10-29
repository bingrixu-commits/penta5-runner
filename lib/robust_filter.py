import numpy as np
from collections import deque

class RobustPredictor:
    def __init__(self, window_size=30, decay_lambda=0.90, monitor_window=10, min_hit_rate=0.5):
        self.history = deque(maxlen=window_size)
        self.hit_monitor = deque(maxlen=monitor_window)
        self.decay_lambda = decay_lambda
        self.min_hit_rate = min_hit_rate

    def update_actual_result(self, actual_value, last_prediction=None):
        self.history.append(actual_value)
        if last_prediction is not None:
            is_hit = 1 if actual_value in last_prediction else 0
            self.hit_monitor.append(is_hit)

    def should_skip(self):
        if len(self.hit_monitor) < self.hit_monitor.maxlen:
            return False
        current_hit_rate = sum(self.hit_monitor) / len(self.hit_monitor)
        return current_hit_rate < self.min_hit_rate

    def calculate_decay_weights(self, length):
        weights = [self.decay_lambda ** (length - 1 - i) for i in range(length)]
        return np.array(weights) / sum(weights)

    def predict(self, candidate_pool):
        if self.should_skip():
            return "SKIP"
        if len(self.history) < 10:
            return "WARMUP"

        data_array = list(self.history)
        weights = self.calculate_decay_weights(len(data_array))

        scores = {num: 0.0 for num in candidate_pool}
        for idx, item in enumerate(data_array):
            if item in scores:
                scores[item] += weights[idx]

        sorted_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        selected_candidates = [item[0] for item in sorted_candidates[:8]]
        return selected_candidates

if __name__ == "__main__":
    predictor = RobustPredictor(window_size=30, decay_lambda=0.90, monitor_window=10, min_hit_rate=0.4)
    np.random.seed(42)
    pool = list(range(10))
    last_pred = None

    for issue in range(1, 101):
        actual = np.random.choice(pool)
        predictor.update_actual_result(actual, last_pred)
        current_pred = predictor.predict(pool)
        print(f"期数 [{issue:03d}] | 状态: {str(current_pred):<25} | 队列长度: {len(predictor.history)}")
        if current_pred not in ["SKIP", "WARMUP"]:
            last_pred = current_pred
        else:
            last_pred = None
