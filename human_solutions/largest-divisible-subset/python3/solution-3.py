def largest_divisible_subset(nums):
    nums.sort()
    n = len(nums)
    dp = [1] * n
    index = [-1] * n
    max_size = 0
    max_index = -1

    for i in range(n):
        for j in range(i - 1, -1, -1):
            if nums[i] % nums[j] == 0 and dp[j] + 1 > dp[i]:
                dp[i] = dp[j] + 1
                index[i] = j

        if dp[i] > max_size:
            max_size = dp[i]
            max_index = i

    result = []
    while max_index != -1:
        result.append(nums[max_index])
        max_index = index[max_index]

    return result

# Example usage
nums = [1, 2, 3, 6, 12, 9, 20]
result = largest_divisible_subset(nums)
print(result)