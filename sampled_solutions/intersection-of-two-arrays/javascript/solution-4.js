function intersection(arr1, arr2) {
    let arr3 = [];
    for (let i = 0; i < arr2.length; i++) {
        if (arr1.includes(arr2[i])) {
            arr3.push(arr2[i]);
        }
    }
    return arr3;
}

console.log(intersection([1, 2, 3, 4, 5], [4, 5, 6, 7, 8])); // Output: [4, 5]