def find_pivot_index(nums):
    global_sum = sum(nums)
    left_sum = 0
    
    for i, num in enumerate(nums):
        if global_sum - 2 * left_sum == num:
            return i
        left_sum += num
    
    return -1

# Test the function
nums = [1, 7, 3, 5, 6]
print(find_pivot_index(nums))  # Output should be 3