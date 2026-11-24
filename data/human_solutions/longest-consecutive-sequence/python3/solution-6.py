def longest_consecutive(nums):
    nums_set = set(nums)
    longest_streak = 0

    for num in nums_set:
        if num - 1 not in nums_set:
            current_num = num
            current_streak = 1

            while current_num + 1 in nums_set:
                current_num += 1
                current_streak += 1

            longest_streak = max(longest_streak, current_streak)

    return longest_streak

# Test the function with examples
nums1 = [10, 4, 200, 1, 3, 2]
print(longest_consecutive(nums1))  # Output: 4

nums2 = [0, 1, 2, 3, 4, 5, 6, 7, 8]
print(longest_consecutive(nums2))  # Output: 9