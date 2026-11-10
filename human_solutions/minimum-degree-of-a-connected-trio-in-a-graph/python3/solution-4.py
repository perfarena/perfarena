def reductionOperation(nums):
    n = len(nums)
    nums.sort()
    answer = 0
    for i in range(n - 1, 0, -1):
        if nums[i] != nums[i - 1]:
            answer += n - i
        else:
            nums[i] = nums[i - 1]
    return answer

# Test the function with the given array
nums = [1, 2, 2, 3]
result = reductionOperation(nums)
print(result)