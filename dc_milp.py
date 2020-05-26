import gurobipy as gp
from gurobipy import GRB
from temporal_network import TemporalNetwork, SimpleContingentTemporalConstraint, SimpleTemporalConstraint
from dc_checker_abstract import DCChecker

class DCCheckerMILP(DCChecker):
    '''
    This implementation uses the MILP formulation from the following paper:

    Optimising Bounds in Simple Temporal Networks with Uncertainty under
    Dynamic Controllability Constraints
    Jing Cui, Peng Yu, Cheng Fang, Patrik Haslum, Brian C. Williams 

    It is also summarized in Casanova's paper in ECAI 2016.
    '''

    def __init__(self, tn):
        self.orig_tn = tn
        self.MAX_NUMERIC_BOUND = 100000

    def is_controllable(self):
        '''
        Return:
        + res: controllable or not
        + None: does not return conflict, see output file 'infeasible.ilp' for IIS.
        '''
        res = self.solve_dc()
        return res, None

    def preprocess_network(self, tn):
        '''
        Given a network, preprocess it so that no contingent constraints
        share the same source event, and no contingent constraint starts
        from an uncontrollable event.
        Returns a shallow copy of the network, with new constraints for
        the modified part of the network
        '''
        network = TemporalNetwork()
        constraints = tn.get_constraints()
        uncontrollable_to_num_contingent = {}
        for c in constraints:
            if isinstance(c, SimpleContingentTemporalConstraint):
                uncontrollable_to_num_contingent[c.e] = 1

        source_to_num_contingent = {}
        for c in constraints:
            if isinstance(c, SimpleContingentTemporalConstraint):
                # c.s is an uncontrollable event
                if c.s in uncontrollable_to_num_contingent:
                    # contingent link starts from an uncontrollable event
                    num_contingent = uncontrollable_to_num_contingent[c.s]
                    source_copy = c.s + str(num_contingent)
                    uncontrollable_to_num_contingent[c.s] = num_contingent + 1
                    equality = SimpleTemporalConstraint(c.s, source_copy, 0, 0, 'equality({},{})'.format(c.s, source_copy))
                    new_c = SimpleContingentTemporalConstraint(source_copy, c.e, c.lb, c.ub, c.name)
                    network.add_constraints([equality, new_c])
                else:
                    # c.s is an executable event
                    if c.s in source_to_num_contingent:
                        # source event already has another contingent link
                        num_contingent = source_to_num_contingent[c.s]
                        source_copy = c.s + str(num_contingent)
                        source_to_num_contingent[c.s] = num_contingent + 1
                        equality = SimpleTemporalConstraint(c.s, source_copy, 0, 0, 'equality({},{})'.format(c.s, source_copy))
                        new_c = SimpleContingentTemporalConstraint(source_copy, c.e, c.lb, c.ub, c.name)
                        network.add_constraints([equality, new_c])
                    else:
                        source_to_num_contingent[c.s] = 1
                        network.add_constraint(c)
            else:
                network.add_constraint(c)
        return network

    def solve_dc(self):

        try:

            self.tn = self.preprocess_network(self.orig_tn)

            # Create a new model
            m = gp.Model("DCchecking")

            # Create variables
            self.add_variables_to_model(m)

            # Set objectives
            # (feasibility problem, no objective)
            # e.g. m.setObjective(x + y + 2 * z, GRB.MAXIMIZE)

            # Add constraints
            self.add_constraints_to_model(m)

            # Optimize model
            m.optimize()

            # Write the LP model
            # m.write('model.lp')

            if m.status == GRB.Status.INFEASIBLE:
                # print('No feasible solution. See infeasible.ilp for conflict.')
                m.computeIIS()
                m.write("infeasible.ilp")
                return False
            else:
                # print('Solution found.')
                # for v in m.getVars():
                #     print('%s %g' % (v.varName, v.x))
                #     print('Obj: %g' % m.objVal)
                return True

        except gp.GurobiError as e:
            print('Error code ' + str(e.errno) + ': ' + str(e))

        except AttributeError:
            print('Encountered an attribute error')

    def add_variables_to_model(self, m):

        events = self.tn.get_events()
        constraints = self.tn.get_constraints()

        # (vi, vj) => variable, vi != vj
        self.u = {}

        for vi in events:
            for vj in events:
                if not vi == vj:
                    uij = m.addVar(lb=-self.MAX_NUMERIC_BOUND, ub=self.MAX_NUMERIC_BOUND, vtype=GRB.CONTINUOUS, name="u({}, {})".format(vi, vj))
                    self.u[(vi, vj)] = uij

        # (vi, vj, vk) => variable, where (vi, vk) is contingent, vj != vk, vj != vi
        self.w = {}

        # (vi, vj, vk) => boolean variable, for each wait var
        self.x = {}

        # (vi, vj, vk) => boolean variable, for precede or not
        self.b = {}

        for c in constraints:
            if isinstance(c, SimpleContingentTemporalConstraint):
                vi = c.s
                vk = c.e
                for vj in events:
                    if not vj == vi and not vj == vk:
                        wijk = m.addVar(lb=-self.MAX_NUMERIC_BOUND, ub=self.MAX_NUMERIC_BOUND, vtype=GRB.CONTINUOUS, name="w({}, {}, {})".format(vi, vj, vk))
                        self.w[(vi, vj, vk)] = wijk
                        xijk = m.addVar(vtype=GRB.BINARY, name="x({}, {}, {})".format(vi, vj, vk))
                        self.x[(vi, vj, vk)] = xijk
                        bijk = m.addVar(vtype=GRB.BINARY, name="b({}, {}, {})".format(vi, vj, vk))
                        self.b[(vi, vj, vk)] = bijk
                        

    def add_constraints_to_model(self, m):

        events = self.tn.get_events()
        constraints = self.tn.get_constraints()

        # Non-negative cycle constraint
        # uij + uji >= 0
        visited = {}
        for (vi, vj) in self.u:
            if not (vi, vj) in visited and not (vj, vi) in visited:
                visited[(vi, vj)] = True
                uij = self.u[(vi, vj)]
                uji = self.u[(vj, vi)]
                m.addConstr(uij + uji >= 0, 'nonneg({},{})'.format(vi, vj))

        # (1), (2) Bounds for requirement and contingent constraints
        for c in constraints:
            # uij <= Uij, lij >= Lij (uji <= -Lij)
            uij = self.u[(c.s, c.e)]
            uji = self.u[(c.e, c.s)]
            if not c.ub is None:
                m.addConstr(uij <= c.ub, 'upperbound({},{})'.format(c.s, c.e))
            if not c.lb is None:
                m.addConstr(uji <= - c.lb, 'lowerbound({},{})'.format(c.e, c.s))

            # if contingent, uij = Uij, lij = Lij
            if isinstance(c, SimpleContingentTemporalConstraint):
                m.addConstr(uij >= c.ub, 'u({}, {}) >= U'.format(c.s, c.e))
                m.addConstr(uji >= - c.lb, 'u({}, {}) >= -L'.format(c.e, c.s))

        # (3) Shortest path constraint
        # uik <= uij + ujk
        for vi in events:
            for vj in events:
                for vk in events:
                    if not vi == vj and not vi == vk and not vj == vk:
                        uij = self.u[(vi, vj)]
                        ujk = self.u[(vj, vk)]
                        uik = self.u[(vi, vk)]
                        m.addConstr(uik <= uij + ujk, 'shortestpath({},{},{})'.format(vi, vj, vk))

        # (4) Precede constraint 
        # ljk > 0 => uij <= lik - ljk (uij <= -uki + ukj) and lij >= uik - ujk (-uji >= uik - ujk)
        for (vi, vj, vk) in self.b:
            # When (vi, vk) is contingent and ljk > 0 means vj precedes vk for sure
            bijk = self.b[(vi, vj, vk)]
            uij = self.u[(vi, vj)]
            uji = self.u[(vj, vi)]
            uik = self.u[(vi, vk)]
            uki = self.u[(vk, vi)]
            ujk = self.u[(vj, vk)]
            ukj = self.u[(vk, vj)]
            # If b = 0, ljk <= 0 (ukj >= 0)
            m.addConstr(ukj + bijk * self.MAX_NUMERIC_BOUND >= 0, 'precede-b0({},{},{})'.format(vi, vj, vk))
            # If b = 1, the other two constraints
            m.addConstr(uij - (1-bijk) * self.MAX_NUMERIC_BOUND <= -uki + ukj, 'precede-b1-a({},{},{})'.format(vi, vj, vk))
            m.addConstr(-uji + (1-bijk) * self.MAX_NUMERIC_BOUND >= uik - ujk, 'precede-b1-b({},{},{})'.format(vi, vj, vk))

        # (5)(6) Wait constraint
        # uik - ujk <= wijk
        # min(lik, wijk) <= lij
        # wijk <= uij should hold according to Cui's, but missed by Casanova
        # TODO: Why is the alpha beta formulation necessary in Cui's paper for (6)?
        for (vi, vj, vk) in self.w:
            # When (vi, vk) is contingent and vj, vk unordered
            uij = self.u[(vi, vj)]
            uji = self.u[(vj, vi)]
            uik = self.u[(vi, vk)]
            uki = self.u[(vk, vi)]
            ujk = self.u[(vj, vk)]
            wijk = self.w[(vi, vj, vk)]
            # uik - ujk <= wijk
            m.addConstr(uik - ujk <= wijk, 'wait({},{},{})'.format(vi, vj, vk))

            # wijk <= uij
            # See Wah and Xin's NLP encoding for why
            # If (vi, vj) is requirement link, then it's possible that needs to wait, if so, wait should be smaller than uij.
            m.addConstr(wijk <= uij, 'wait<ub({},{},{})'.format(vi, vj, vk))

            # min(lik, wijk) <= lij
            # (lij >= lik and wijk >= lik) or (lij >= wijk and wijk <= lik)
            # Reason: If wijk >= lik, we need that ukj + uji (uji = -lij <= -wijk) + lik(should be uik, but ik is contingent) >= 0 based on shortest path,
            # then ukj >= -lik - uji >= -lik + wijk >= 0, meaning vj can happen after vk, 
            # Should be fine without comparing wijk >=/<= lik, but (7) uses it in Peng's implementation
            xijk = self.x[(vi, vj, vk)]
            # If x = 0, lij >= lik (uji <= uki) and -uki <= wijk
            m.addConstr(uji - xijk * self.MAX_NUMERIC_BOUND <= uki, 'waitcond0({},{},{})'.format(vi, vj, vk))
            m.addConstr(wijk + xijk * self.MAX_NUMERIC_BOUND >= -uki, 'waitcond0+({},{},{})'.format(vi, vj, vk))
            # If x = 1, lij >= wijk (-uji >= wijk) and -uki >= wijk
            m.addConstr(-uji + (1-xijk) * self.MAX_NUMERIC_BOUND >= wijk, 'waitcond1({},{},{})'.format(vi, vj, vk))
            m.addConstr(wijk - (1-xijk) * self.MAX_NUMERIC_BOUND <= -uki, 'waitcond1+({},{},{})'.format(vi, vj, vk))

            # Note that in Wah and Xin's paper, there is also wijk = lij if (vi, vj) is contingent
            # We can preprocess the network to avoid contingent links sharing same source event,
            # by adding a copy of the source event and add equality constraint.
            # Cui's paper may have assumed so.
            
        # (8) wait regression
        # wijk − umj <= wimk
        for c in constraints:
            if isinstance(c, SimpleContingentTemporalConstraint):
                vi = c.s
                vk = c.e
                for vj in events:
                    for vm in events:
                        if not vj == vi and not vj == vk and not vm == vi and not vm == vk and not vm == vj:
                            wijk = self.w[(vi, vj, vk)]
                            wimk = self.w[(vi, vm, vk)]
                            umj = self.u[(vm, vj)]
                            m.addConstr(wijk - umj <= wimk, 'regression({},{},{},{})'.format(vi, vj, vk, vm))

        # (7) wait regression for contingent constraint
        # (wijk <= 0) or (wijk − lmj <= wimk)
        # In Peng's implementation, and according to Cui's paper, can be strengthed to
        # (wijk >= lik) => (wijk − lmj <= wimk)
        # That is xijk = 0 => (wijk + ujm <= wimk)
        # TODO: this should be correct, though unclear what this (wijk <= 0) condition really means
        for c1 in constraints:
            if isinstance(c1, SimpleContingentTemporalConstraint):
                for c2 in constraints:
                    if isinstance(c2, SimpleContingentTemporalConstraint):
                        vi = c1.s
                        vk = c1.e
                        vm = c2.s
                        vj = c2.e
                        if not c1 == c2:
                            # Note that we assume vm == vi is avoided by copying vi to vi' and add equality constraint
                            assert(not vm == vi)
                            wijk = self.w[(vi, vj, vk)]
                            xijk = self.x[(vi, vj, vk)]
                            wimk = self.w[(vi, vm, vk)]
                            ujm = self.u[(vj, vm)]
                            m.addConstr(wijk + ujm - xijk * self.MAX_NUMERIC_BOUND <= wimk, 'regression-contingent({},{},{},{})'.format(vi, vj, vk, vm))
