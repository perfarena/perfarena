void DFS(Node node, double Target, int K, Q<int> Q) {
    if (node == null) return;
    DFS(node.left, Target, K, Q);
    
    // Check if the queue has space to add the current node value
    if (Q.Count < K) {
        Q.Enqueue(node.value);
    } else {
        // Compare current node value with values in the queue
        double distanceToTarget = Math.Abs(node.value - Target);
        double distanceToFirstInQueue = Math.Abs(Q.Peek() - Target);
        
        if (distanceToTarget < distanceToFirstInQueue) {
            Q.Dequeue(); // Remove the first element in the queue
            Q.Enqueue(node.value); // Add the current node value
        }
    }
    
    DFS(node.right, Target, K, Q);
}