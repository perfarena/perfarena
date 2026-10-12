let set1 = new Set(nums1);
let set2 = new Set(nums2);

let intersection = [];
for (let value of set1) {
    if (set2.has(value)) {
        intersection.push(value);
    }
}

return intersection;