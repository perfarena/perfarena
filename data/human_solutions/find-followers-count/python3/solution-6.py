def minimize_max_peer_sum(nums):
    nums.sort()
    result = 0
    for i in range(len(nums) // 2):
        result = max(result, nums[i] + nums[-i - 1])
    return result

# Test the function
nums = [3, 5, 4, 2, 4, 6]
print(minimize_max_peer_sum(nums))