import os
import numpy as np
import explainability_techniques.PDP as pdp
from sklearn.neighbors import KNeighborsClassifier
from util import vecPredictProba
from util import cartesian_product


class CustomPlanner:
    def __init__(self, X, n_neighbors, n_startingSolutions,
                 reqClassifiers, targetConfidence,
                 controllableFeaturesNames,
                 controllableFeatureIndices,
                 controllableFeatureDomains,
                 optimizationDirections,
                 optimizationScoreFunction,
                 delta=1, plotsPath=None):

        self.n_neighbors = n_neighbors
        self.n_startingSolutions = n_startingSolutions
        self.reqClassifiers = reqClassifiers
        self.targetConfidence = targetConfidence
        self.controllableFeatureIndices = np.array(controllableFeatureIndices)
        self.controllableFeatureDomains = controllableFeatureDomains
        self.optimizationDirections = optimizationDirections
        self.optimizationScoreFunction = optimizationScoreFunction
        self.delta = delta

        # train a k nearest neighbors classifier only used to find the neighbors of a sample in the dataset
        knn = KNeighborsClassifier()
        knn.fit(X, np.zeros((X.shape[0], 1)))
        self.knn = knn

        # make pdps
        self.pdps = {}
        for i, feature in enumerate(controllableFeaturesNames):
            self.pdps[i] = []
            for j, reqClassifier in enumerate(reqClassifiers):
                path = None
                if plotsPath is not None:
                    path = plotsPath + "/req_" + str(j)
                    if not os.path.exists(path):
                        os.makedirs(path)
                self.pdps[i].append(pdp.partialDependencePlot(reqClassifier, X, [feature], "both", path + "/" + feature + ".png"))

        # make summary pdps
        self.summaryPdps = []
        for i, feature in enumerate(controllableFeaturesNames):
            path = None
            if plotsPath is not None:
                path = plotsPath + "/summary"
                if not os.path.exists(path):
                    os.makedirs(path)
            self.summaryPdps.append(pdp.multiplyPdps(self.pdps[i], path + "/" + feature + ".png"))

    def optimizeScoreStep(self, adaptation, confidence, isValidAdaptation, excludedFeatures, tempExcludedFeatures):
        # select a feature to modify
        featureIndex = None
        controllableIndex = None
        minConfidenceLoss = None
        for i, index in enumerate(self.controllableFeatureIndices):
            if index not in excludedFeatures and index not in tempExcludedFeatures:
                neighborIndex = np.ravel(self.knn.kneighbors([adaptation], 1, False))[0]
                slope = pdp.getSlope(self.summaryPdps[i], adaptation[index], neighborIndex)
                confidenceLoss = slope * self.optimizationDirections[i]
                if minConfidenceLoss is None or confidenceLoss < minConfidenceLoss:
                    featureIndex = index
                    controllableIndex = i
                    minConfidenceLoss = confidenceLoss

        # return if no feature can be modified
        if featureIndex is None:
            return None, None

        # modify the selected feature
        newAdaptation = np.copy(adaptation)
        newAdaptation[featureIndex] += self.optimizationDirections[controllableIndex] * self.delta

        featureMin = self.controllableFeatureDomains[controllableIndex, 0]
        featureMax = self.controllableFeatureDomains[controllableIndex, 1]

        if newAdaptation[featureIndex] < featureMin:
            newAdaptation[featureIndex] = featureMin
            excludedFeatures.append(featureIndex)
        elif newAdaptation[featureIndex] > featureMax:
            newAdaptation[featureIndex] = featureMax
            excludedFeatures.append(featureIndex)

        newConfidence = vecPredictProba(self.reqClassifiers, [newAdaptation])[0]

        if (isValidAdaptation and (newConfidence < self.targetConfidence).any()) \
                or (not isValidAdaptation and (newConfidence < confidence).any()):
            newAdaptation = np.copy(adaptation)
            newConfidence = np.copy(confidence)
            tempExcludedFeatures.append(featureIndex)
        else:
            tempExcludedFeatures.clear()

        return newAdaptation, newConfidence

    def findAdaptation(self, row):
        n_controllableFeatures = len(self.controllableFeatureIndices)

        # find neighbors
        neighbors = np.ravel(self.knn.kneighbors([row], self.n_neighbors, False))

        # starting solutions
        adaptations = [row]
        for i in range(self.n_neighbors):
            neighborIndex = neighbors[i]
            adaptation = np.copy(row)

            recalculateNeighbor = False
            excludedFeatures = []
            while len(excludedFeatures) < n_controllableFeatures:
                # recalculate neighbor after the first step
                if recalculateNeighbor:
                    neighborIndex = np.ravel(self.knn.kneighbors([adaptation], 1, False))[0]
                recalculateNeighbor = True

                maxYVals = [(j, pdp.getMaxOfLine(self.summaryPdps[j], neighborIndex)) for j in range(n_controllableFeatures)]
                maxYVals = sorted(maxYVals, key=lambda val: val[1], reverse=True)

                for val in maxYVals:
                    controllableIndex = val[0]
                    if controllableIndex not in excludedFeatures:
                        maximals = pdp.getMaximalsOfLine(self.summaryPdps[controllableIndex], neighborIndex)
                        if self.optimizationDirections[controllableIndex] == -1:
                            # leftmost maximal
                            x = maximals[0]
                        else:
                            # rightmost maximal
                            x = maximals[len(maximals) - 1]

                        newAdaptation = np.copy(adaptation)
                        newAdaptation[self.controllableFeatureIndices[controllableIndex]] = x
                        adaptations.append(newAdaptation)
                        excludedFeatures.append(controllableIndex)
                        break

        # remove duplicate solutions
        adaptations = np.unique(adaptations, axis=0)

        for adaptation in adaptations:
            neighborIndex = np.ravel(self.knn.kneighbors([adaptation], 1, False))[0]

            maximals = [pdp.getMaximalsOfLine(self.summaryPdps[i], neighborIndex) for i in
                        range(n_controllableFeatures)]

            maxPossibilities = 10000
            n_possibilities = np.prod([len(m) for m in maximals])
            while n_possibilities > maxPossibilities:
                i = np.argmax([len(m) for m in maximals])
                maximals[i] = maximals[i][1::2]
                n_possibilities = np.prod([len(m) for m in maximals])

            possibilities = cartesian_product(*maximals)
            possibilities = np.append(possibilities,
                                      np.repeat([row[n_controllableFeatures:]], possibilities.shape[0], axis=0),
                                      axis=1)

            """
            print(len(possibilities))
            p = vecPredictProba(self.reqClassifiers, possibilities, axis=1)
            print(np.max(p))
            """

            adaptations = np.append(adaptations, possibilities, axis=0)

        # remove duplicate solutions again
        adaptations = np.unique(adaptations, axis=0)

        adaptationsConfidence = vecPredictProba(self.reqClassifiers, adaptations)

        """
        print("\nStarting adaptations:")
        print(adaptations[:, :n_controllableFeatures])
        print("\nStarting adaptations confidence:")
        print(adaptationsConfidence)
        print("\nStarting adaptations score:")
        print([self.scoreFunction(a) for a in adaptations])
        """

        validAdaptationIndices = []
        for i, confidence in enumerate(adaptationsConfidence):
            if (confidence >= self.targetConfidence).all():
                validAdaptationIndices.append(i)

        validAdaptations = adaptations[validAdaptationIndices]
        validAdaptationsConfidence = adaptationsConfidence[validAdaptationIndices]
        validAdaptationFound = len(validAdaptations) > 0

        if validAdaptationFound and len(validAdaptations) > self.n_startingSolutions:
            # pick the best n solutions:
            # rank solutions based on success proba and score
            def solutionRank(a, c):
                return (self.optimizationScoreFunction(a) +
                        np.sum(c - self.targetConfidence) / np.sum(np.ravel(np.ones((1, len(self.reqClassifiers)))) - self.targetConfidence) * 100)

            validAdaptationsRanks = [solutionRank(validAdaptations[i], validAdaptationsConfidence[i])
                                     for i in range(len(validAdaptations))]

            bestAdaptationIndices = np.argpartition(validAdaptationsRanks, -self.n_startingSolutions)[-self.n_startingSolutions:]
            bestAdaptations = validAdaptations[bestAdaptationIndices]
            bestAdaptationsConfidence = validAdaptationsConfidence[bestAdaptationIndices]

            """
            print("\nStarting valid adaptations ranking:")
            print(validAdaptationsRanks)
            """
            print("Best starting valid adaptations: " + str(bestAdaptationIndices))
        elif validAdaptationFound:
            # just keep the starting adaptations
            bestAdaptations = validAdaptations
            bestAdaptationsConfidence = validAdaptationsConfidence
            print("Starting valid adaptations:")
        else:
            # no valid adaptation found, so:
            # rank solutions based on success proba only
            def solutionRank(c):
                return np.sum(c - self.targetConfidence)

            adaptationsRanks = [solutionRank(adaptationsConfidence[i]) for i in range(len(adaptations))]

            # pick maximum proba solutions
            bestAdaptationIndices = np.where(adaptationsRanks == np.max(adaptationsRanks))
            bestAdaptations = adaptations[bestAdaptationIndices]
            bestAdaptationsConfidence = adaptationsConfidence[bestAdaptationIndices]

            # select the best n based on the score
            if len(bestAdaptations) > self.n_startingSolutions:
                bestAdaptationsScores = [self.optimizationScoreFunction(a) for a in bestAdaptations]
                bestAdaptationIndices = np.argpartition(bestAdaptationsScores, -self.n_startingSolutions)[-self.n_startingSolutions:]
                bestAdaptations = bestAdaptations[bestAdaptationIndices]
                bestAdaptationsConfidence = bestAdaptationsConfidence[bestAdaptationIndices]

            """
            print("\nStarting adaptations ranking:")
            print(adaptationsRanks)
            """
            print("Best starting adaptations: " + str(bestAdaptationIndices))

        print(bestAdaptations[:, :n_controllableFeatures])
        print("\nBest starting adaptations confidence:")
        print(bestAdaptationsConfidence)

        # enhance solutions
        optimizationSteps = [0] * len(bestAdaptations)

        for i in range(len(bestAdaptations)):
            excludedFeatures = []
            tempExcludedFeatures = []
            adaptation = bestAdaptations[i]
            confidence = bestAdaptationsConfidence[i]
            while len(excludedFeatures) + len(tempExcludedFeatures) < n_controllableFeatures:
                adaptation, confidence = self.optimizeScoreStep(adaptation, confidence, validAdaptationFound, excludedFeatures, tempExcludedFeatures)
                optimizationSteps[i] += 1

            bestAdaptations[i] = adaptation
            bestAdaptationsConfidence[i] = confidence

        print("\nScore optimization steps: " + str(optimizationSteps))

        # remove duplicate solutions (there can be new duplicates after the optimization phase)
        bestAdaptations, indices = np.unique(bestAdaptations, axis=0, return_index=True)
        bestAdaptationsConfidence = bestAdaptationsConfidence[indices]

        print("\nFinal adaptations:")
        print(bestAdaptations[:, :n_controllableFeatures])

        print("\nFinal adaptations confidence:")
        print(bestAdaptationsConfidence)

        bestAdaptationsScores = [self.optimizationScoreFunction(a) for a in bestAdaptations]
        print("\nFinal adaptations scores:")
        print(bestAdaptationsScores)

        finalAdaptationIndex = np.argmax(bestAdaptationsScores)
        finalAdaptation = bestAdaptations[finalAdaptationIndex]
        finalAdaptationConfidence = bestAdaptationsConfidence[finalAdaptationIndex]
        print("\nBest final adaptation: " + str(finalAdaptationIndex))
        print(finalAdaptation[:n_controllableFeatures])
        print("With confidence:")
        print(finalAdaptationConfidence)
        print()

        return finalAdaptation, finalAdaptationConfidence, bestAdaptationsScores[finalAdaptationIndex]
