def max_consecutive_ones(nums, K):
    window = []
    best_window_size = 0
    current_zeros = 0

    for num in nums:
        if num == 1:
            window.append(1)
        elif num == 0:
            current_zeros += 1
            while current_zeros > K:
                if window[0] == 0:
                    current_zeros -= 1
                window.pop(0)
        
        best_window_size = max(best_window_size, len(window))

    return best_window_size