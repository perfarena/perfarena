def two_sum(nums, target):
    hash_table = {}
    for i, num in enumerate(nums):
        diff = target - num
        if diff in hash_table and hash_table[diff] != i:
            return [hash_table[diff], i]
        hash_table[num] = i
    return []