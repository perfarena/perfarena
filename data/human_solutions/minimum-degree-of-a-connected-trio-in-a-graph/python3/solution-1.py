def minOperations(nums):
    nums.sort()
    n = len(nums)
    operations = 0
    
    for i in range(n - 1):
        if nums[i] != nums[i + 1]:
            operations += n - i - 1
    
    return operations

# Test the function with examples
print(minOperations([5, 1, 3]))  # Output: 3
print(minOperations([1, 2, 2, 3]))  # Output: 6