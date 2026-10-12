# Given two integer arrays nums1 and nums2, return an array of their intersection
def find_intersection(nums1, nums2):
    common = []
    for x in nums2:
        if x in nums1:
            common.append(x)
    return list(set(common))

# Example usage
nums1 = [1, 2, 2, 1]
nums2 = [2, 2]
result = find_intersection(nums1, nums2)
print(result)