from typing import List

def two_sum(nums: List[int], target: int) -> List[int]:
    mappings = {}
    for index, value in enumerate(nums):
        required_value = target - value
        if required_value in mappings and mappings[required_value] != index:
            return [mappings[required_value], index]
        mappings[value] = index

# Sample Test Cases
print(two_sum([2, 7, 11, 15], 9))  # Output: [0, 1]
print(two_sum([3, 2, 4], 6))       # Output: [1, 2]