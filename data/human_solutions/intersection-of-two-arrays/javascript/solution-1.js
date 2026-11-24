function intersection(num1, num2) {
    let firstSet = new Set();
    let intersectionSet = new Set();

    // Add elements from num1 to firstSet
    for (let num of num1) {
        firstSet.add(num);
    }

    // Find shared elements and add to intersectionSet
    for (let num of num2) {
        if (firstSet.has(num)) {
            intersectionSet.add(num);
        }
    }

    // Convert intersectionSet to array and return
    return Array.from(intersectionSet);
}