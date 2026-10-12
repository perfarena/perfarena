import collections

def find_intersection(nums1, nums2):
    counter1 = collections.Counter(nums1)
    counter2 = collections.Counter(nums2)
    
    intersection = counter1 & counter2
    
    return list(intersection.elements())

# Example usage
nums1 = [1, 2, 2, 1]
nums2 = [2, 2]
result = find_intersection(nums1, nums2)
print(result)