def minimize_max_pair_sum(nums):
    nums.sort()
    n = len(nums)
    meanMaxSum = 0
    
    for i in range(n // 2):
        currentMinSum = nums[i] + nums[n - 1 - i]
        meanMaxSum = max(meanMaxSum, currentMinSum)
    
    return meanMaxSum