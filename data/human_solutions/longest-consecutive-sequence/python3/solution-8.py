def longest_consecutive(nums):
    s = set(nums)
    longest = 0
    
    for num in nums:
        if num - 1 not in s:
            current_num = num
            while current_num + 1 in s:
                current_num += 1
            longest = max(longest, current_num - num + 1)
    
    return longest

# Test the function
nums = [100, 4, 200, 1, 3, 2]
print(longest_consecutive(nums))  # Output: 4