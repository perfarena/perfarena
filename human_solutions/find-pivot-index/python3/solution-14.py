def find_pivot_index(nums):
    left_sum = 0
    total_sum = sum(nums)
    
    for index in range(len(nums)):
        if left_sum == total_sum - nums[index] - left_sum:
            return index
        left_sum += nums[index]
    
    return -1