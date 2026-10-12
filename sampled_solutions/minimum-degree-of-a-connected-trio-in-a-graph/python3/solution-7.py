def min_operations(nums):
    nums.sort(reverse=True)
    operations = 0
    for i in range(1, len(nums)):
        if nums[i] != nums[i - 1]:
            operations += i
    return operations

# Test the function with sample input
nums = [1, 3, 4, 7, 6, 3, 5]
result = min_operations(nums)
print(result)