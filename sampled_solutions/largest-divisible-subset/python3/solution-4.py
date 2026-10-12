def largest_divisible_subset(nums):
    nums.sort()
    n = len(nums)
    dp = [1] * n
    dp_pred = [-1] * n
    max_size = 1
    max_index = 0

    for i in range(n):
        for j in range(i):
            if nums[i] % nums[j] == 0 and dp[j] + 1 > dp[i]:
                dp[i] = dp[j] + 1
                dp_pred[i] = j
                if dp[i] > max_size:
                    max_size = dp[i]
                    max_index = i

    subset = []
    while max_index != -1:
        subset.append(nums[max_index])
        max_index = dp_pred[max_index]

    return subset