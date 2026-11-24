def reduction_operations(nums):
    nums.sort()
    answer = 0
    changes = 0
    
    for i in range(1, len(nums)):
        if nums[i] != nums[i - 1]:
            changes += 1
            answer += changes
    
    return answer

# Example usage
nums = [5, 1, 3]
result = reduction_operations(nums)
print(result)  # Output: 3