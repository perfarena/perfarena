def minimize_max_pair_sum(nums):
    for i in range(len(nums) // 2):
        M = max(M, nums[i] + nums[-1 - i])
    return M