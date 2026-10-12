def find_pivot_index(nums):
    l_sum = 0
    sum_total = sum(nums)
    
    for i in range(len(nums)):
        sum_total -= nums[i]
        
        if l_sum == sum_total:
            return i
        
        l_sum += nums[i]
    
    return -1

# Test the function with the example test case
nums = [1, 7, 3, 6, 5, 6]
print(find_pivot_index(nums))  # Output should be 3