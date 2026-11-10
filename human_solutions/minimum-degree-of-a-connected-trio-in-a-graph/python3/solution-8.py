def minOperations(nums):
    nums.sort(reverse=True)
    result = 0
    current_num = None
    
    for i, num in enumerate(nums):
        if current_num != num:
            current_num = num
            result += i
    
    return result

# Test the function with examples
print(minOperations([5, 1, 3]))  # Output should be 2
print(minOperations([5, 1, 3, 2, 1, 2]))  # Output should be 8