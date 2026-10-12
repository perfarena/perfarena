def sign_of_product(nums):
    count = 0
    has_zero = False
    
    for num in nums:
        if num == 0:
            return 0
        if num < 0:
            count += 1
    
    return 1 if count % 2 == 0 else -1

# Test the function
nums = [2, -3, 4, 0]
result = sign_of_product(nums)
print(result)