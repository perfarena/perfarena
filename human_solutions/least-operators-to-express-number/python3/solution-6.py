def max_consecutive_ones(nums, K):
    ones_count = 0
    max_length = 0
    window_start = 0

    for window_end in range(len(nums)):
        if nums[window_end] == 1:
            ones_count += 1

        if (window_end - window_start + 1 - ones_count) > K:
            if nums[window_start] == 1:
                ones_count -= 1
            window_start += 1

        max_length = max(max_length, window_end - window_start + 1)

    return max_length

# Test the function
nums = [1, 0, 1, 1, 0, 1]
K = 1
print(max_consecutive_ones(nums, K))  # Output: 4