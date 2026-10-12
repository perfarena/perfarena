# Sort the elements in ascending order
sorted_nums = sorted(nums)

# Pick the two largest elements
largest_product = sorted_nums[-1] * sorted_nums[-2]

# Pick the two smallest elements
smallest_product = sorted_nums[0] * sorted_nums[1]

# Calculate the maximum product difference
max_product_difference = largest_product - smallest_product

return max_product_difference