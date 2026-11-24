def firstMissingPositive(nums):
    n = len(nums)
    
    for i in range(n):
        while 1 <= nums[i] <= n and nums[nums[i] - 1] != nums[i]:
            nums[nums[i] - 1], nums[i] = nums[i], nums[nums[i] - 1]
    
    for i in range(n):
        if nums[i] != i + 1:
            return i + 1
    
    return n + 1

# Test the function
nums = [3, 4, -1, 1]
result = firstMissingPositive(nums)
print(result)  # Output: 2