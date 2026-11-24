def longest_consecutive(nums):
    all_nums = set(nums)
    longest = 0

    for num in all_nums:
        if num - 1 not in all_nums:
            current = 1
            while num + current in all_nums:
                current += 1
            longest = max(longest, current)

    return longest