function searchInsert(numbers, target) {
    let low = 0;
    let high = numbers.length - 1;

    while (low <= high) {
        let mid = Math.floor((low + high) / 2);

        if (numbers[mid] === target) {
            return mid;
        } else if (numbers[mid] < target) {
            low = mid + 1;
        } else {
            high = mid - 1;
        }
    }

    return low;
}