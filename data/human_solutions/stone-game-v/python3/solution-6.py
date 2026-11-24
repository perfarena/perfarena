def getSumAbsoluteDifferences(nums):
    n = len(nums)
    total_sum = sum(nums)
    answer = [0] * n
    l_sum = 0

    for i in range(n):
        answer[i] = nums[i] * i - l_sum + total_sum - l_sum - nums[i] * (n - i)
        l_sum += nums[i]

    return answer

# Test the function
nums = [2, 3, 5, 6, 8, 10]
result = getSumAbsoluteDifferences(nums)
print(result)