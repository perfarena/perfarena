def minOperations(nums):
    nums.sort()
    result = 0
    temporary = -1
    count = -1
    
    for num in nums:
        if num != temporary:
            temporary = num
            count += 1
            result += count
    
    return result

# Example test case
nums = [5, 1, 3]
print(minOperations(nums))  # Output should be 3