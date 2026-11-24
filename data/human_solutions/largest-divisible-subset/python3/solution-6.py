def largestDivisibleSubset(nums):
    # Sort the input array
    nums.sort()
    
    # Initialize variables
    n = len(nums)
    dp = [1] * n
    prev = [-1] * n
    max_len = 1
    max_idx = 0
    
    # Dynamic programming to find the largest divisible subset
    for i in range(n):
        for j in range(i):
            if nums[i] % nums[j] == 0 and dp[j] + 1 > dp[i]:
                dp[i] = dp[j] + 1
                prev[i] = j
                if dp[i] > max_len:
                    max_len = dp[i]
                    max_idx = i
    
    # Reconstruct the subset
    result = []
    while max_idx != -1:
        result.append(nums[max_idx])
        max_idx = prev[max_idx]
    
    return result[::-1]