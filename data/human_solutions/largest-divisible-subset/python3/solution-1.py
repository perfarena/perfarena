def largestDivisibleSubset(nums):
    nums.sort()
    n = len(nums)
    dp = [[num] for num in nums]
    result = []

    for i in range(n - 1, -1, -1):
        for j in range(i + 1, n):
            if nums[j] % nums[i] == 0 and len(dp[j]) + 1 > len(dp[i]):
                dp[i] = dp[j] + [nums[i]]

        if len(dp[i]) > len(result):
            result = dp[i]

    return result