def increasing_triplet(nums):
    first = second = float('inf')
    
    for num in nums:
        if num <= first:
            first = num
        elif num <= second:
            second = num
        else:
            return True
    
    return False

# Test the solution
nums = [1, 2, 3, 4, 5]
print(increasing_triplet(nums))  # Output: True

nums = [5, 4, 3, 2, 1]
print(increasing_triplet(nums))  # Output: False

nums = [0, 4, 6]
print(increasing_triplet(nums))  # Output: True