from collections import Counter

def intersect(nums1, nums2):
    mapping = Counter(nums1) if len(nums1) <= len(nums2) else Counter(nums2)
    res = []
    
    for num in nums1 if len(nums1) <= len(nums2) else nums2:
        if num in mapping and mapping[num] > 0:
            res.append(num)
            mapping[num] -= 1
    
    return res

# Test cases
print(intersect([1, 2, 2, 1], [2, 2]))  # Output: [2, 2]