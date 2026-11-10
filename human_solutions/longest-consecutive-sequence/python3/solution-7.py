def longest_consecutive_sequence(nums):
    num_set = set(nums)
    answer = 0
    
    for num in nums:
        if num - 1 not in num_set:
            current = num
            while current + 1 in num_set:
                current += 1
            answer = max(answer, current - num + 1)
    
    return answer

# Test the function with sample test cases
nums = [100, 2013, 2]
print(longest_consecutive_sequence(nums))  # Output: 4