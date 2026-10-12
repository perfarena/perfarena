def longest_consecutive(nums):
    nums = set(nums)
    longest_streak = 0

    for num in nums:
        if num - 1 not in nums:
            current = num
            current_streak = 1

            while current + 1 in nums:
                current += 1
                current_streak += 1

            longest_streak = max(longest_streak, current_streak)

    return longest_streak