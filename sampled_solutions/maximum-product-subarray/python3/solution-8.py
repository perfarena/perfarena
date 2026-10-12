def max_product_subarray(nums):
    if not nums:
        return 0

    max_product = min_product = result = nums[0]

    for num in nums[1:]:
        if num == 0:
            max_product = min_product = 1
        else:
            max_product, min_product = max(num, max_product * num, min_product * num), min(num, max_product * num, min_product * num)
        
        result = max(result, max_product)

    return max(result, 0)

# Example test case
nums = [-2, 3, -2, 4]
print(max_product_subarray(nums))  # Output: 48