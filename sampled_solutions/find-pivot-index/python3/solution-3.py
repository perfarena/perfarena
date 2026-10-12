for i, value in enumerate(nums):
    right_sum = total_sum - left_sum - value
    if right_sum == left_sum:
        return i
    left_sum += value