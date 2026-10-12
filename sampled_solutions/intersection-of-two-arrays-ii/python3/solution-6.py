# Sort the arrays
nums1.sort()
nums2.sort()

# Use two pointers
p1, p2 = 0, 0
result = []
while p1 < len(nums1) and p2 < len(nums2):
    if nums1[p1] == nums2[p2]:
        result.append(nums1[p1])
        p1 += 1
        p2 += 1
    elif nums1[p1] < nums2[p2]:
        p1 += 1
    else:
        p2 += 1