def max_product_difference(nums):
    sorted_nums = sorted(nums)
    min_nums = sorted_nums[:2]
    max_nums = sorted_nums[-2:]
    
    max_product_diff = (max_nums[1] * max_nums[0]) - (min_nums[1] * min_nums[0])
    
    return max_product_diff

# Test the function
nums = [2, 4, 5, 7, 8, 9]
result = max_product_difference(nums)
print(result)