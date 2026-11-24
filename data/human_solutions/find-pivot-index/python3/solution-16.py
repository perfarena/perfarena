def find_pivot_index(nums):
    total_sum = sum(nums)
    left_sum = 0
    
    for index, value in enumerate(nums):
        if left_sum == total_sum - left_sum - value:
            return index
        left_sum += value
    
    return -1

# Test the function
nums = [1, 7, 3, 6, 5, 6]
result = find_pivot_index(nums)
print("Pivot index:", result)