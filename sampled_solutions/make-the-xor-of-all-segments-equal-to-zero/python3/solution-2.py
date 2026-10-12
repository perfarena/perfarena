# Initialize variables to track the four elements
biggest = second_biggest = float('-inf')
smallest = second_smallest = float('inf')

# Iterate over the array nums
for num in nums:
    if num > biggest:
        second_biggest = biggest
        biggest = num
    elif num > second_biggest:
        second_biggest = num

    if num < smallest:
        second_smallest = smallest
        smallest = num
    elif num < second_smallest:
        second_smallest = num

# Calculate the maximum product difference
max_product_diff = (biggest * second_biggest) - (smallest * second_smallest)

return max_product_diff